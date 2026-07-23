import os
import base64
from dotenv import load_dotenv

load_dotenv()

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from datetime import timedelta

SCOPES = ['https://www.googleapis.com/auth/calendar']


def restore_google_files():
    """
    Restore credentials.json & token.json dari environment variable base64
    (dipakai saat deploy di server, karena file JSON tidak ikut ke git).
    """
    token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
    cred_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")

    token_b64 = os.getenv("GOOGLE_TOKEN_B64")
    cred_b64 = os.getenv("GOOGLE_CREDENTIALS_B64")

    if token_b64 and not os.path.exists(token_path):
        with open(token_path, "wb") as f:
            f.write(base64.b64decode(token_b64))

    if cred_b64 and not os.path.exists(cred_path):
        with open(cred_path, "wb") as f:
            f.write(base64.b64decode(cred_b64))


def get_calendar_service():
    restore_google_files()

    token_path = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
    cred_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")

    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token expired tapi ada refresh_token -> refresh otomatis, tanpa browser
            creds.refresh(Request())
        else:
            # Jalur ini HANYA untuk development lokal (butuh browser)
            if os.getenv("ENV") == "local":
                flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                raise RuntimeError(
                    "Google token tidak valid dan tidak ada refresh_token yang bisa dipakai. "
                    "Generate ulang token.json di lokal, encode ke base64, "
                    "lalu update GOOGLE_TOKEN_B64 di environment variables server."
                )

        # Simpan token terbaru (hasil refresh atau login baru) ke file
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def create_event(summary, description, start_time):
    service = get_calendar_service()

    end_time = start_time + timedelta(hours=1)

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'Asia/Jakarta',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'Asia/Jakarta',
        },
    }

    event = service.events().insert(
        calendarId='primary',
        body=event
    ).execute()

    return event.get('htmlLink')