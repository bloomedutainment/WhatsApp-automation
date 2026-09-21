# WhatsApp-Drive-Bot

A FastAPI service that receives files (images and videos) sent via WhatsApp and uploads them to Google Drive.

---

## Overview

This bot is used by Bloom to automate intake of files sent over WhatsApp — instead of manually collecting images/videos from users and uploading them to Drive, the bot receives them automatically via the WhatsApp Cloud API and handles the download, deduplication, and upload pipeline on its own.

---

## Features

- Receives a WhatsApp media ID, resolves it via the Meta API to a media URL, and downloads the file
- Computes a SHA-256 hash of each file while downloading, to check uniqueness by content (not by media ID — so the same file resent, forwarded, or redelivered by a retried webhook is only ever processed once)
- Uses Upstash Redis (REST API) to check for duplicates:
  - If the hash already exists → discards the file (already downloaded before)
  - If not → checks whether it's already been uploaded; if not, uploads it to Drive
- Handles both images and videos, uploading each to its own designated Drive folder
- Can create a per-sender folder (named by the sender's phone number) in Drive and route that sender's files there
- SQLite stores the status of each file (saved / uploaded / duplicate_content)
- Accesses Google Drive via an authenticated OAuth client

---

## Architecture / How It Works

1. App starts → ensures upload directories and the SQLite database exist
2. Meta verifies the webhook once via `GET /webhook` (token check)
3. Meta sends incoming messages to `POST /webhook`
4. Text messages → replied to directly, inline
5. Image/video messages → the media ID is handed to a **background task**, and the endpoint returns a response immediately
   - This matters because Meta expects a fast acknowledgment (a few seconds). If the webhook is still busy downloading/uploading a large file when it responds, Meta assumes delivery failed and **resends** the same message — causing duplicate processing. Running the heavy work in the background avoids this.
6. Background task (the pipeline):
   - Resolve the media URL from the media ID
   - Download the file while simultaneously computing its SHA-256 hash (same pass, no re-read)
   - Check Upstash Redis: has this content hash been downloaded before? Been uploaded before?
   - If new → upload to the correct Drive folder (image/video, optionally per-sender)
   - Update the file's status in SQLite
   - Reply to the sender with the result

---

## Tech Stack

| Tool | Why it's used |
|---|---|
| **Python** | Main language for the service |
| **FastAPI** | Web framework for the webhook endpoints; its async support fits well with scheduling background download/upload work without blocking the response |
| **Uvicorn** | The ASGI server that actually runs the FastAPI app — FastAPI defines the logic, Uvicorn is what listens on the network and hands requests to it |
| **Meta WhatsApp Cloud API** | Used directly (no BSP) to receive incoming messages/media and send replies |
| **Google Drive API (OAuth)** | Used to upload files. OAuth was chosen over a service account because service accounts have no storage quota of their own — uploads through one would fail since there's no space behind it to write into |
| **SQLite** | Lightweight, file-based database used to persist the status of each file (saved / uploaded / duplicate) |
| **Upstash Redis (REST API)** | Fast, in-memory-backed lookup for duplicate detection. Uses the REST API over HTTPS (port 443) instead of raw Redis TCP, since HTTPS is far less likely to be blocked by local networks/antivirus. Uses `SET ... NX` (set-if-not-exists) so the "mark as done" operation is atomic — two near-simultaneous uploads of the same file can't both think they're first. Dedup records expire after 7 days |
| **ngrok** | Tunnels the local FastAPI server to a public URL so Meta's webhook can reach it during development — a dev-only tool; production would need a real, permanent public host |

---

## Prerequisites

**Runtime**
- Python 3.14.5

**External accounts/services**
- A Meta Developer app with WhatsApp Cloud API access (permanent access token + webhook verify token)
- A Google Cloud project with the Drive API enabled and an OAuth client configured
- An Upstash Redis database (REST API)
- ngrok (or equivalent) for exposing the local server to Meta during development

**Python packages** (from `requirements.txt`, install via `pip install -r requirements.txt`)
- Web server: `fastapi`, `starlette`, `uvicorn`, `httptools`, `watchfiles`, `websockets`
- Google Drive integration: `google-api-python-client`, `google-api-core`, `google-auth`, `google-auth-httplib2`, `googleapis-common-protos`, `proto-plus`, `protobuf`, `uritemplate`, `httplib2`
- Redis client: `redis`, `upstash_redis`
- HTTP requests (WhatsApp API calls): `requests`, `urllib3`, `idna`, `charset-normalizer`, `certifi`
- Data validation: `pydantic`, `pydantic_core`, `annotated-types`, `annotated-doc`, `typing-inspection`, `typing_extensions`
- Config/env: `python-dotenv`, `PyYAML`
- Security/crypto (used by Google auth): `cryptography`, `pyasn1`, `pyasn1_modules`, `cffi`, `pycparser`
- Async utilities: `anyio`, `click`, `h11`
- Misc: `opentelemetry-api`, `pyparsing`

---

## Setup / Installation

1. **Clone the repo**
   ```powershell
   git clone https://github.com/bloomedutainment/WhatsApp-automation
   cd whatsapp-drive-bot
   ```

2. **Create and activate a virtual environment**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. **Install dependencies**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Create a `.env` file** in the project root:
   ```
   # WhatsApp / Meta Cloud API
   WHATSAPP_ACCESS_TOKEN=
   WHATSAPP_PHONE_NUMBER_ID=
   WEBHOOK_VERIFY_TOKEN=

   # Google Drive
   GOOGLE_OAUTH_CLIENT_FILE=
   GOOGLE_DRIVE_FOLDER_ID=
   GOOGLE_DRIVE_FOLDER_ID_VIDEO=

   # Redis (Upstash — REST API)
   UPSTASH_REDIS_REST_URL=
   UPSTASH_REDIS_REST_TOKEN=
   ```
   *(Leave the values blank here — fill in your own credentials using the guides below, and never commit this file.)*

5. **Database** — no migrations needed. SQLite initializes automatically on startup via `db.init_db()`.

6. **Redis** — no local setup needed. Redis is hosted via Upstash; just make sure the REST URL and token in `.env` are correct.

7. **Run the server**
   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```

8. **Install ngrok** (for exposing the local server to Meta)
   - Download ngrok for Windows from [https://ngrok.com/download](https://ngrok.com/download).
   - Copy `ngrok.exe` into your project folder (`whatsapp-drive-bot`) in VS Code.
     *(The exact path will depend on where the project lives on your machine — adjust accordingly.)*

9. **Run ngrok** (must be running alongside the app — the app receiving requests from Meta depends on it)
   - Open a terminal and navigate to the project folder:
     ```powershell
     cd C:\Users\Eng\Desktop\Bloom_project\whatsapp-drive-bot
     ```
   - Start ngrok, pointing it at port 8000:
     ```powershell
     .\ngrok.exe http 8000 --request-header-add "ngrok-skip-browser-warning:true"
     ```
   - ⚠️ **Keep this terminal running** the whole time the app needs to receive WhatsApp messages — if ngrok stops, Meta can no longer reach your local server.
   - Find the **Forwarding** URL shown in the ngrok output (e.g. `https://xxxx.ngrok-free.app`) — copy it. You'll paste this into the **Callback URL** field in the Meta webhook configuration (see the WhatsApp credentials guide below).

---

## Getting Your Credentials

### 1. WhatsApp Cloud API (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WEBHOOK_VERIFY_TOKEN`)

1. Go to [https://developers.facebook.com/](https://developers.facebook.com/) and sign in (create an account first if you don't have one).
2. Click **My Apps**, then select your app from the list.
3. On the app dashboard, find the **"Connect with customers through WhatsApp"** use case and click **Customize**.
4. Go to the **Basic setup** step.
5. Click **Generate token**.
   - ⚠️ **Important:** this token expires after a period of time. You'll need to generate a new one periodically, or the bot will stop working until you do.
   - Copy this value → this is your `WHATSAPP_ACCESS_TOKEN`.
6. Still on this page, find the **test number** section — there's a phone number listed there.
   - Copy that number's ID → this is your `WHATSAPP_PHONE_NUMBER_ID`.
7. Under **"Send a message from your test number,"** you can add a recipient number, so your test number can send messages to that phone for testing.
8. On the left-hand menu, go to **Step 2** and click **Production setup**.
9. Under **Callback URL**, paste the public URL ngrok gave you (e.g. `https://xxxx.ngrok.io/webhook`).
10. Under **Verify token**, enter a token of your choosing.
    - This is your `WEBHOOK_VERIFY_TOKEN` — you pick this value yourself, then put the same value in `.env` so the two match.

### 2. Google OAuth Client (`GOOGLE_OAUTH_CLIENT_FILE`)

1. Go to [https://console.cloud.google.com/](https://console.cloud.google.com/) and sign in.
2. Use the search bar at the top and type **"Audience"** — click into it.
3. Scroll down to **Test users** and click **Add users** — add the Google account(s) allowed to use this app while it's in testing mode.
4. Go back to the main Cloud Console page.
5. Click the **three horizontal lines (menu)** in the top-left corner.
6. Click **APIs & Services**, then **Credentials**.
7. Click **OAuth consent screen**, then go to **Clients**.
8. Click **Add client** (or **Create client**).
9. Choose **Application type: Desktop app**, give it a name, and click **Create**.
10. Click on the client you just created to open its details.
11. Scroll down to **Client secrets**.
12. Click the **download button** next to the secret — this downloads a `.json` file.
13. Move the file into your project folder in VS Code (the `whatsapp-drive-bot` folder).
14. In `.env`, set `GOOGLE_OAUTH_CLIENT_FILE` to that file's name (e.g. `GOOGLE_OAUTH_CLIENT_FILE=client_secret_XXXXXXXX.json`).

### 3. Google Drive Folder IDs (`GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_DRIVE_FOLDER_ID_VIDEO`)

1. Open the folder in Google Drive where uploaded files should go.
2. Look at the URL — it'll look like `https://drive.google.com/drive/folders/18hbLrQIPTCwBKOIaA216b1j_8UJL3uGB`
3. Copy everything after `/folders/` — that string is your folder ID.
4. Repeat for both your images folder (`GOOGLE_DRIVE_FOLDER_ID`) and your videos folder (`GOOGLE_DRIVE_FOLDER_ID_VIDEO`), if they're separate.

### 4. Upstash Redis (`UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`)

1. Go to [https://console.upstash.com/redis](https://console.upstash.com/redis) and sign in.
2. Click **Create database**.
3. Click on the database you just created.
4. Scroll down to the **Connect** section.
5. Under **REST**, copy the two values shown and paste them into `.env`:
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`

---

*Sections still to add: Usage, Project structure, Known limitations, Roadmap, Contributors.*