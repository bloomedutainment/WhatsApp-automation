import logging
import os
import hashlib
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse

from app import db, drive, whatsapp, dedup

# --- Config ---
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN")
UPLOADS_DIR = "uploads"
UPLOAD_VID = "upload_videos"
logging.basicConfig(
    filename='app.log',
    filemode='a',
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__file__)
app = FastAPI()


@app.on_event("startup")
def on_startup():
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    os.makedirs(UPLOAD_VID, exist_ok=True)
    db.init_db()


@app.get("/")
def health_check():
    return {"status": "ok"}


@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def receive_whatsappmessages(request: Request, background_tasks: BackgroundTasks):
    """IMPORTANT: this function must return a response FAST. Meta
    expects an acknowledgment within a few seconds - if the webhook
    takes too long to respond (e.g. because it's busy downloading a
    large video and uploading it to Drive before replying), Meta
    assumes delivery failed and RESENDS the same message, which is
    what was causing the repeated retries. To fix this, the actual
    download/upload work is scheduled as a background task via
    background_tasks.add_task(...) - this function returns
    immediately after scheduling it, so Meta gets its fast
    acknowledgment right away and has no reason to retry."""
    body = await request.json()

    try:
        value = body["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
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
        sender = message["from"]
        msg_type = message["type"]

        if msg_type == "text":
            text_body = message["text"]["body"]
            logger.info(f"Message from {sender}: {text_body}")
            _safe_reply(sender, f"Got your message: {text_body}")
            return {"status": "text_received"}

        if msg_type != "image" and msg_type != "video":
            print(msg_type)
            _safe_reply(sender, "I can only process images and videos right now")
            return {"status": "ignored_non_image"}

        if msg_type == "image":
            media_id = message["image"]["id"]
            logger.info(f"Image received from {sender} (media_id={media_id})")
            background_tasks.add_task(run_pipeline, media_id, sender, 'i')
        if msg_type == "video":
            video_id = message["video"]["id"]
            logger.info(f"Video received from {sender} (media_id={video_id})")
            background_tasks.add_task(run_pipeline_Video, video_id, sender, 'v')

    except (KeyError, IndexError):
        return {"status": "ignored"}

    return {"status": "received"}


def run_pipeline(media_id, sender, file_type):
    """The image pipeline: download from WhatsApp -> save locally ->
    upload to Google Drive -> reply to the user with the result.

    Dedup is based on a SHA-256 hash of the file's actual bytes,
    computed WHILE the file streams in from WhatsApp (same loop that
    writes it to disk). That one hash is then used as the key for
    BOTH the "already downloaded" and "already uploaded" checks -
    so the same photo content is only ever saved/uploaded once, even
    if it arrives under a different media_id each time (resent,
    forwarded, or a retried webhook delivery).
    """
    local_path = os.path.join(UPLOADS_DIR, f"{media_id}.jpg")
    file_already_exists = False

    try:
        media_url = whatsapp.get_media_url(media_id)
        if not media_url:
            raise RuntimeError("Could not resolve media URL")

        # Download + hash in the same pass: as each chunk arrives from
        # WhatsApp, it's written to disk AND fed into the hash at the
        # same time - one read of the stream, not a separate re-read.
        #
        # The folder has been observed disappearing between the
        # makedirs() check above and this open() call (something
        # external is removing it mid-flight - antivirus, sync tool,
        # etc.). Retrying with a fresh makedirs() call makes this
        # resilient to that, regardless of the external cause.
        image_iterator = whatsapp.download_media(media_url)
        sha256_hash = hashlib.sha256()
        for attempt in range(3):
            try:
                os.makedirs(UPLOADS_DIR, exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in image_iterator:
                        f.write(chunk)
                        sha256_hash.update(chunk)
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                logger.error(f"uploads folder vanished mid-write, retrying (attempt {attempt + 1}/3)")
                image_iterator = whatsapp.download_media(media_url)  # re-fetch: the previous iterator is partially consumed
                sha256_hash = hashlib.sha256()
        file_hash = sha256_hash.hexdigest()
        logger.info(f"Saved image locally: {local_path} (hash={file_hash})")

        # --- Same hash, first check: has this content been saved before? ---
        if dedup.is_downloaded(file_hash):
            logger.info(f"Content hash {file_hash} already downloaded before")
            file_already_exists = True
        else:
            dedup.mark_downloaded(file_hash)
            db.insert_message(file_hash, sender, local_path, status="saved")

        # --- Same hash, second check: has this content been uploaded before? ---
        if dedup.is_uploaded(file_hash):
            logger.info(f"Content hash {file_hash} already uploaded before - skipping upload")
            db.update_status(file_hash, "duplicate_content")
            _safe_reply(sender, "Already processed this one")
            if file_already_exists:
                os.remove(local_path)
            return

        drive_file_id = drive.upload_file(local_path, f"{file_hash}.jpg", file_type=file_type, sender=sender)
        dedup.mark_uploaded(file_hash)
        db.update_status(file_hash, "uploaded", drive_file_id)
        logger.info(f"Uploaded to Drive: {drive_file_id}")
        _safe_reply(sender, "uploaded")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        _safe_reply(sender, "Upload failed, we'll retry shortly.")


def run_pipeline_Video(media_id, sender, file_type):
    local_path = os.path.join(UPLOAD_VID, f"{media_id}.mp4")
    file_already_exists = False
    try:
        media_url = whatsapp.get_media_url(media_id)
        if not media_url:
            raise RuntimeError("Could not resolve media URL")

        video_iterator = whatsapp.download_media(media_url)
        sha256_hash = hashlib.sha256()
        for attempt in range(3):
            try:
                os.makedirs(UPLOAD_VID, exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in video_iterator:
                        f.write(chunk)
                        sha256_hash.update(chunk)
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                logger.error(f"upload_videos folder vanished mid-write, retrying (attempt {attempt + 1}/3)")
                video_iterator = whatsapp.download_media(media_url)
                sha256_hash = hashlib.sha256()
        file_hash = sha256_hash.hexdigest()
        logger.info(f"video saved locally: {local_path} (hash={file_hash})")

        if dedup.is_downloaded(file_hash):
            logger.info(f"Content hash {file_hash} already downloaded before")
            file_already_exists = True
        else:
            dedup.mark_downloaded(file_hash)
            db.insert_message(file_hash, sender, local_path, status="saved")

        if dedup.is_uploaded(file_hash):
            logger.info(f"Content hash {file_hash} already uploaded before - skipping upload")
            db.update_status(file_hash, "duplicate_content")
            _safe_reply(sender, "Already processed this one")
            if file_already_exists:
                os.remove(local_path)
            return

        drive_file_id = drive.upload_file(local_path, f"{file_hash}.mp4", 'video/mp4', file_type=file_type, sender=sender)
        dedup.mark_uploaded(file_hash)
        db.update_status(file_hash, "uploaded", drive_file_id)
        logger.info(f"Uploaded to Drive: {drive_file_id}")
        _safe_reply(sender, "uploaded")
    except Exception as e:
        logger.error(f"Video Pipeline failed: {e}")
        _safe_reply(sender, "Video Upload failed, we'll retry shortly.")


def _safe_reply(sender, text):
    print(f"[reply to {sender}]: {text}")