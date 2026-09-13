import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from app import db, drive, whatsapp

# --- Config ---
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN")
UPLOADS_DIR = "uploads"

# Reuse uvicorn's own logger so our log lines show up cleanly
# alongside uvicorn's request logs in the same terminal.
logger = logging.getLogger("uvicorn")

app = FastAPI()


@app.on_event("startup")
def on_startup():
    """Runs once when the server starts: make sure the uploads folder
    exists and the local database/table is created."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    db.init_db()


@app.get("/")
def health_check():
    """Simple endpoint to confirm the server is alive. Visiting this
    URL in a browser or ngrok should return {"status": "ok"}."""
    return {"status": "ok"}


@app.get("/webhook")
def verify_webhook(request: Request):
    """Meta calls this ONCE when you register/verify the webhook URL
    in the App Dashboard. It sends hub.mode, hub.verify_token, and
    hub.challenge as query params. If our token matches what's in
    .env, we echo the challenge back and Meta considers us verified."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request):
    """Meta calls this every time a real event happens (a message is
    sent to your bot's number, a status update, etc.). This is the
    main entry point for all incoming WhatsApp activity."""
    body = await request.json()

    try:
        value = body["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            # This is a status update (sent/delivered/read/failed).
            # Log it so we can see WHY a message didn't arrive -
            # WhatsApp reports delivery failures here, with a reason.
            if "statuses" in value:
                status_info = value["statuses"][0]
                status = status_info.get("status")
                recipient = status_info.get("recipient_id")
                logger.info(f"Status update: {status} for {recipient}")
                if status == "failed":
                    errors = status_info.get("errors", [])
                    for err in errors:
                        logger.error(f"Delivery failed: {err.get('title')} - {err.get('message')}")
            return {"status": "ignored"}

        message = value["messages"][0]
        sender = message["from"]      # the sender's phone number
        msg_type = message["type"]    # "text", "image", etc.

        # --- Text messages: just log + echo back what was received ---
        if msg_type == "text":
            text_body = message["text"]["body"]
            logger.info(f"Message from {sender}: {text_body}")
            _safe_reply(sender, f"Got your message: {text_body}")
            return {"status": "text_received"}

        # --- Anything that isn't text or image: politely decline ---
        if msg_type != "image":
            _safe_reply(sender, "I can only process images right now")
            return {"status": "ignored_non_image"}

        # --- Image messages: run the full save -> upload pipeline ---
        media_id = message["image"]["id"]
        logger.info(f"Image received from {sender} (media_id={media_id})")
        run_pipeline(media_id, sender)

    except (KeyError, IndexError):
        # Payload didn't have the shape we expected — ignore it
        # rather than crashing the server.
        return {"status": "ignored"}

    return {"status": "received"}


def run_pipeline(media_id, sender):
    """The image pipeline: download from WhatsApp -> save locally ->
    upload to Google Drive -> reply to the user with the result."""
    local_path = os.path.join(UPLOADS_DIR, f"{media_id}.jpg")

    # Step 1: resolve the media_id into a real, temporary download URL,
    # then download the actual image bytes and save them to disk.
    media_url = whatsapp.get_media_url(media_id)
    image_bytes = whatsapp.download_media(media_url)
    with open(local_path, "wb") as f:
        f.write(image_bytes)
    logger.info(f"Saved image locally: {local_path}")

    db.insert_message(media_id, sender, local_path, status="saved")

    # Step 2: upload the saved file to Google Drive. If this fails
    # (e.g. missing/invalid credentials), don't crash — log it,
    # mark it in the DB, and tell the user we'll retry.
    try:
        drive_file_id = drive.upload_file(local_path, f"{media_id}.jpg")
        db.update_status(media_id, "uploaded", drive_file_id)
        logger.info(f"Uploaded to Drive: {drive_file_id}")
        _safe_reply(sender, "uploaded")
    except Exception as e:
        logger.error(f"Drive upload failed: {e}")
        db.update_status(media_id, "upload_failed")
        _safe_reply(sender, "Upload failed, we'll retry shortly.")


def _safe_reply(sender, text):
    """Send a WhatsApp reply, but never let a failed reply (e.g. an
    unverified test recipient, expired token) crash the request. The
    pipeline's own work (saving the file, updating the DB) has
    already happened by the time we get here - a reply that can't be
    delivered shouldn't turn that into a 500 error."""
    try:
        whatsapp.send_text_message(sender, text)
    except Exception as e:
        logger.error(f"Could not send reply to {sender}: {e}")