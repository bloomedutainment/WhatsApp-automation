import os
from pathlib import Path

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# OAuth client file (Desktop app type) - downloaded from Cloud Console
# Credentials -> Create Credentials -> OAuth client ID -> Desktop app
OAUTH_CLIENT_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "oauth_client.json")

# Where the user's authorized token gets cached after the first login,
# so you don't have to log in again every time the server restarts.
TOKEN_FILE = Path(__file__).parent.parent / "token.json"

FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_drive_service = None


def get_drive_service():
    """Authenticates as YOUR Google account (not a service account),
    so uploads use your own storage quota and work with any regular
    Drive folder you have access to - including ones shared with you.

    The first time this runs, it opens a browser window asking you to
    log in and approve access. After that, the approval is cached in
    token.json and reused automatically - no repeated logins."""
    global _drive_service
    if _drive_service is not None:
        return _drive_service

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

    _drive_service = build("drive", "v3", credentials=creds)
    return _drive_service


def upload_file(local_path: str, filename: str, mimetype: str = "image/jpeg") -> str:
    """Upload a local file to the configured Drive folder, using your
    own Google account's storage quota. Returns the Drive file ID."""
    service = get_drive_service()
    file_metadata = {"name": filename, "parents": [FOLDER_ID]} if FOLDER_ID else {"name": filename}
    media = MediaFileUpload(local_path, mimetype=mimetype)
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return uploaded["id"]