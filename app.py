import os
import json
import asyncio
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import discord
import dateparser
from fastapi import FastAPI

from gcal import create_event

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

discord_client = discord.Client(intents=intents)

REMINDER_FILE = "reminders.json"


# =========================
# STORAGE
# =========================
def load_reminders():
    try:
        with open(REMINDER_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_reminders(data):
    with open(REMINDER_FILE, "w") as f:
        json.dump(data, f, indent=2)


# =========================
# PARSER WAKTU (ADVANCED)
# =========================
def parse_time(text):
    now = datetime.now()
    text = text.lower()

    time_match = re.search(r'(\d{1,2})[.:](\d{2})', text)
    hour, minute = None, None

    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))

    if "lusa" in text:
        base_date = now + timedelta(days=2)
    elif "besok" in text:
        base_date = now + timedelta(days=1)
    elif "hari ini" in text:
        base_date = now
    else:
        parsed = dateparser.parse(
            text,
            languages=['id', 'en'],
            settings={'PREFER_DATES_FROM': 'future'}
        )
        base_date = parsed if parsed else now

    if hour is not None:
        dt = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        dt = base_date

    if dt < now:
        dt += timedelta(days=1)

    return dt


# =========================
# AMBIL ISI MESSAGE
# =========================
def extract_message(ref):
    return (
        ref.content
        or (ref.embeds[0].description if ref.embeds else "")
        or "[No Text]"
    )


# =========================
# DISCORD EVENTS
# =========================
@discord_client.event
async def on_ready():
    print(f"Logged in as {discord_client.user}")
    discord_client.loop.create_task(reminder_loop())


@discord_client.event
async def on_message(message):
    if message.author.bot:
        return

    print("📩 Pesan masuk:", message.content)

    if not message.reference:
        return

    if not message.content.lower().startswith("remind"):
        return

    ref = await message.channel.fetch_message(message.reference.message_id)
    original = extract_message(ref)

    command = message.content.lower().replace("remind", "").strip()
    remind_time = parse_time(command)

    if not remind_time:
        await message.reply("❌ Format waktu tidak kebaca")
        return

    try:
        event_link = create_event(
            summary=original[:50],
            description=original,
            start_time=remind_time
        )
    except Exception as e:
        print("❌ Gagal Google Calendar:", e)
        event_link = None

    reminder = {
        "user_id": message.author.id,
        "text": original,
        "time": remind_time.isoformat()
    }

    data = load_reminders()
    data.append(reminder)
    save_reminders(data)

    response = (
        f"⏰ Reminder dibuat\n"
        f"📝 {original}\n"
        f"📅 {remind_time.strftime('%d %b %Y %H:%M')}\n"
    )

    if event_link:
        response += f"\n📆 Google Calendar:\n{event_link}"

    await message.reply(response)


# =========================
# SCHEDULER
# =========================
async def reminder_loop():
    await discord_client.wait_until_ready()

    while True:
        now = datetime.now()
        data = load_reminders()
        new_data = []

        for r in data:
            t = datetime.fromisoformat(r["time"])

            if now >= t:
                user = await discord_client.fetch_user(r["user_id"])
                try:
                    await user.send(f"⏰ Reminder!\n\n📝 {r['text']}")
                except:
                    pass
            else:
                new_data.append(r)

        save_reminders(new_data)
        await asyncio.sleep(10)


# =========================
# FASTAPI APP + LIFESPAN
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Discord bot sebagai background task saat FastAPI startup
    bot_task = asyncio.create_task(discord_client.start(TOKEN))
    yield
    # Cleanup saat shutdown
    await discord_client.close()
    bot_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "ok", "bot_logged_in": discord_client.is_ready()}


@app.get("/health")
async def health():
    return {"status": "healthy"}