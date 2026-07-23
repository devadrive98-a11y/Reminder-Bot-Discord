import json
import uuid

REMINDER_FILE = "reminders.json"
SETTINGS_FILE = "user_settings.json"


# =========================
# LOW LEVEL JSON HELPERS
# =========================
def _load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# =========================
# REMINDERS
# =========================
def load_reminders():
    return _load_json(REMINDER_FILE, [])


def save_reminders(data):
    _save_json(REMINDER_FILE, data)


def add_reminder(user_id, text, time_iso, repeat="none", calendar_link=None):
    """
    repeat: "none" | "daily" | "weekly"
    """
    data = load_reminders()
    reminder = {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "text": text,
        "time": time_iso,
        "repeat": repeat,
        "calendar_link": calendar_link,
    }
    data.append(reminder)
    save_reminders(data)
    return reminder


def remove_reminder(reminder_id):
    data = load_reminders()
    new_data = [r for r in data if r["id"] != reminder_id]
    save_reminders(new_data)
    return len(new_data) != len(data)


def update_reminder_time(reminder_id, new_time_iso):
    data = load_reminders()
    for r in data:
        if r["id"] == reminder_id:
            r["time"] = new_time_iso
    save_reminders(data)


def get_user_reminders(user_id):
    data = load_reminders()
    return [r for r in data if r["user_id"] == user_id]


# =========================
# USER SETTINGS (TIMEZONE)
# =========================
def load_settings():
    return _load_json(SETTINGS_FILE, {})


def save_settings(data):
    _save_json(SETTINGS_FILE, data)


def get_user_timezone(user_id):
    settings = load_settings()
    return settings.get(str(user_id), {}).get("timezone", "Asia/Jakarta")


def set_user_timezone(user_id, tz_name):
    settings = load_settings()
    settings.setdefault(str(user_id), {})["timezone"] = tz_name
    save_settings(settings)


def get_user_snooze_minutes(user_id):
    settings = load_settings()
    return settings.get(str(user_id), {}).get("snooze_minutes", 10)


def set_user_snooze_minutes(user_id, minutes):
    settings = load_settings()
    settings.setdefault(str(user_id), {})["snooze_minutes"] = minutes
    save_settings(settings)


def get_user_event_color(user_id):
    """Mengembalikan Google Calendar colorId (None = default warna kalender)."""
    settings = load_settings()
    return settings.get(str(user_id), {}).get("event_color")


def set_user_event_color(user_id, color_id):
    settings = load_settings()
    settings.setdefault(str(user_id), {})["event_color"] = color_id
    save_settings(settings)