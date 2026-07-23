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

# Mapping nama warna -> Google Calendar colorId
EVENT_COLORS = {
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",
}


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
            creds.refresh(Request())
        else:
            if os.getenv("ENV") == "local":
                flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                raise RuntimeError(
                    "Google token tidak valid dan tidak ada refresh_token yang bisa dipakai. "
                    "Generate ulang token.json di lokal, encode ke base64, "
                    "lalu update GOOGLE_TOKEN_B64 di environment variables server."
                )

        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def create_event(summary, description, start_time, repeat="none", timezone_name="Asia/Jakarta",
                  color_id=None, reminder_minutes=10):
    """
    repeat: "none" | "daily" | "weekly"
    color_id: Google Calendar colorId, "1".."11", atau None untuk warna default
    """
    service = get_calendar_service()

    end_time = start_time + timedelta(hours=1)

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': timezone_name,
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': timezone_name,
        },
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': reminder_minutes},
            ],
        },
    }

    if color_id:
        event['colorId'] = str(color_id)

    if repeat == "daily":
        event['recurrence'] = ['RRULE:FREQ=DAILY']
    elif repeat == "weekly":
        event['recurrence'] = ['RRULE:FREQ=WEEKLY']

    event = service.events().insert(
        calendarId='primary',
        body=event
    ).execute()

    return event.get('htmlLink')