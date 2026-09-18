import os
import threading
from pathlib import Path
import logging
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# OAuth client file (Desktop app type) - downloaded from Cloud Console
# Credentials -> Create Credentials -> OAuth client ID -> Desktop app
OAUTH_CLIENT_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "oauth_client.json")
# what authenticates me for google drive
# Where the user's authorized token gets cached after the first login,
# so you don't have to log in again every time the server restarts.
TOKEN_FILE = Path(__file__).parent.parent / "token.json"

FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_ID_VIDEO = os.getenv("GOOGLE_DRIVE_FOLDER_ID_VIDEO")

# IMPORTANT: since run_pipeline now runs as a FastAPI background task,
# multiple uploads can execute concurrently in different threads.
# googleapiclient's underlying HTTP connection (httplib2) is NOT
# thread-safe - sharing one connection across threads caused the
# corrupted-upload errors (mismatched Content-Range sizes, SSL wrong
# version number, garbled HttpError responses). threading.local()
# gives each thread its own separate Drive service/connection, so
# concurrent uploads no longer interfere with each other.
_thread_local = threading.local()

# In-memory cache of sender -> their subfolder's Drive ID, so we don't
# search/create the same folder on every single upload. Keyed by
# (parent_folder_id, sender) since a sender needs a separate folder
# under the images parent vs. the videos parent. Dictionaries are
# safe to share across threads for simple get/set like this.
_sender_folder_cache = {}
_cache_lock = threading.Lock()

logger = logging.getLogger(__file__)


def get_drive_service():
    """Authenticates as YOUR Google account (not a service account),
    so uploads use your own storage quota and work with any regular
    Drive folder you have access to - including ones shared with you.

    The first time this runs (per thread), it opens a browser window
    asking you to log in and approve access - but token.json is
    written after the very first successful login, so every
    subsequent call (including from other threads) reuses that saved
    token instead of opening the browser again."""
    if getattr(_thread_local, "drive_service", None) is not None:
        return _thread_local.drive_service

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(OAUTH_CLIENT_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    _thread_local.drive_service = build("drive", "v3", credentials=creds)
    return _thread_local.drive_service


def get_or_create_sender_folder(sender: str, parent_folder_id: str) -> str:
    """Returns the Drive folder ID for this sender's subfolder inside
    parent_folder_id - creating it if it doesn't exist yet, or
    returning the existing one if it does. This is the Drive
    equivalent of os.makedirs(path, exist_ok=True): safe to call every
    time, never creates a duplicate folder for a sender who already
    has one.

    _cache_lock protects the shared cache dict from two threads
    reading/writing it at the exact same moment (a race that could,
    in rare cases, cause a duplicate folder to be created)."""
    cache_key = (parent_folder_id, sender)
    with _cache_lock:
        if cache_key in _sender_folder_cache:
            return _sender_folder_cache[cache_key]

    service = get_drive_service()

    # Search for an existing folder named exactly `sender`, inside
    # parent_folder_id, that isn't in the trash.
    query = (
        f"name = '{sender}' and "
        f"'{parent_folder_id}' in parents and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    existing = results.get("files", [])

    if existing:
        folder_id = existing[0]["id"]
        logger.info(f"Found existing Drive folder for {sender}: {folder_id}")
    else:
        folder_metadata = {
            "name": sender,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_folder_id],
        }
        created = service.files().create(body=folder_metadata, fields="id").execute()
        folder_id = created["id"]
        logger.info(f"Created new Drive folder for {sender}: {folder_id}")

    with _cache_lock:
        _sender_folder_cache[cache_key] = folder_id
    return folder_id


def upload_file(local_path: str, filename: str, mimetype: str = "image/jpeg", file_type: str = 'i', sender: str = None) -> str:
    """Upload a local file to the configured Drive folder, using your
    own Google account's storage quota. If `sender` is provided, the
    file goes into a per-sender subfolder inside the appropriate
    top-level folder (images or videos) - created automatically if it
    doesn't exist yet. Returns the Drive file ID."""
    service = get_drive_service()
    top_level_folder = FOLDER_ID_VIDEO if file_type == 'v' else FOLDER_ID

    parent_id = top_level_folder
    if sender and top_level_folder:
        parent_id = get_or_create_sender_folder(sender, top_level_folder)

    file_metadata = {"name": filename, "parents": [parent_id]} if parent_id else {"name": filename}
    media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]