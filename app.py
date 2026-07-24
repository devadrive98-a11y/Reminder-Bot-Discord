import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager

import discord
from discord import app_commands
from discord.ext import commands
import dateparser
import aiohttp
from fastapi import FastAPI

from gcal import create_event, EVENT_COLORS
import storage

TOKEN = os.getenv("DISCORD_TOKEN")
HEALTH_CHECK_URL = os.getenv(
    "HEALTH_CHECK_URL",
    "https://reminder-bot-discord-3a3b97d1.fastapicloud.dev/"
)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =========================
# PARSER WAKTU (TIMEZONE-AWARE)
# =========================
def parse_time(text, tz_name="Asia/Jakarta"):
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    text = text.lower()

    # deteksi repeat keyword, lalu bersihkan dari text sebelum parsing waktu
    repeat = "none"
    if "setiap hari" in text or "every day" in text or "daily" in text:
        repeat = "daily"
        text = text.replace("setiap hari", "").replace("every day", "").replace("daily", "").strip()
    elif "setiap minggu" in text or "every week" in text or "weekly" in text:
        repeat = "weekly"
        text = text.replace("setiap minggu", "").replace("every week", "").replace("weekly", "").strip()

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
            settings={
                'PREFER_DATES_FROM': 'future',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'TIMEZONE': tz_name,
            }
        )
        base_date = parsed if parsed else now
        if base_date.tzinfo is None:
            base_date = base_date.replace(tzinfo=tz)

    if hour is not None:
        dt = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        dt = base_date

    if dt < now:
        dt += timedelta(days=1)

    return dt, repeat


def extract_message(ref):
    return (
        ref.content
        or (ref.embeds[0].description if ref.embeds else "")
        or "[No Text]"
    )


# =========================
# SHARED LOGIC: BUAT REMINDER
# =========================
async def build_reminder(user_id, text, remind_time, repeat, timezone_name):
    """
    Buat event Google Calendar + simpan reminder lokal.
    Mengembalikan (reminder_dict, calendar_link, calendar_error)
    """
    calendar_link = None
    calendar_error = None
    color_id = storage.get_user_event_color(user_id)

    try:
        calendar_link = create_event(
            summary=text[:50],
            description=text,
            start_time=remind_time,
            repeat=repeat,
            timezone_name=timezone_name,
            color_id=color_id,
        )
    except Exception as e:
        calendar_error = str(e)
        print("❌ Gagal Google Calendar:", e)

    reminder = storage.add_reminder(
        user_id=user_id,
        text=text,
        time_iso=remind_time.isoformat(),
        repeat=repeat,
        calendar_link=calendar_link,
    )

    return reminder, calendar_link, calendar_error


def format_reminder_response(text, remind_time, repeat, calendar_link, calendar_error):
    repeat_label = {"none": "", "daily": " (berulang setiap hari)", "weekly": " (berulang setiap minggu)"}[repeat]

    response = (
        f"⏰ Reminder dibuat{repeat_label}\n"
        f"📝 {text}\n"
        f"📅 {remind_time.strftime('%d %b %Y %H:%M %Z')}\n"
    )

    if calendar_link:
        response += f"\n📆 Google Calendar:\n{calendar_link}"
    elif calendar_error:
        response += (
            "\n⚠️ Reminder tersimpan, tapi gagal sync ke Google Calendar.\n"
            f"Detail error: `{calendar_error[:150]}`"
        )

    return response


# =========================
# CONFIRMATION VIEW
# =========================
class ConfirmView(discord.ui.View):
    def __init__(self, author_id, on_confirm):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ini bukan reminder kamu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Konfirmasi", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        await interaction.response.defer()
        await self.on_confirm(interaction)

    @discord.ui.button(label="❌ Batal", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="❌ Reminder dibatalkan.", view=None)


# =========================
# SNOOZE VIEW (dipakai saat reminder terpicu)
# =========================
class SnoozeView(discord.ui.View):
    def __init__(self, user_id, text, timezone_name, snooze_minutes=10):
        super().__init__(timeout=3600)  # tombol aktif 1 jam sejak notif dikirim
        self.user_id = user_id
        self.text = text
        self.timezone_name = timezone_name
        self.snooze_minutes = snooze_minutes

        # ganti label tombol sesuai durasi snooze user
        self.snooze.label = f"⏰ Snooze {snooze_minutes} menit"

    @discord.ui.button(label="⏰ Snooze 10 menit", style=discord.ButtonStyle.secondary)
    async def snooze(self, interaction: discord.Interaction, button: discord.ui.Button):
        tz = ZoneInfo(self.timezone_name)
        new_time = datetime.now(tz) + timedelta(minutes=self.snooze_minutes)

        storage.add_reminder(
            user_id=self.user_id,
            text=self.text,
            time_iso=new_time.isoformat(),
            repeat="none",
        )

        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"⏰ Oke, diingatkan lagi jam {new_time.strftime('%H:%M')}.",
            ephemeral=True,
        )


# =========================
# DISCORD EVENTS (legacy reply-based flow)
# =========================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print("Gagal sync slash command:", e)
    bot.loop.create_task(reminder_loop())


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.reference:
        return

    if not message.content.lower().startswith("remind"):
        return

    ref = await message.channel.fetch_message(message.reference.message_id)
    original = extract_message(ref)

    command = message.content.lower().replace("remind", "").strip()
    tz_name = storage.get_user_timezone(message.author.id)
    remind_time, repeat = parse_time(command, tz_name)

    if not remind_time:
        await message.reply("❌ Format waktu tidak kebaca")
        return

    async def on_confirm(interaction):
        reminder, calendar_link, calendar_error = await build_reminder(
            message.author.id, original, remind_time, repeat, tz_name
        )
        response = format_reminder_response(original, remind_time, repeat, calendar_link, calendar_error)
        await interaction.edit_original_response(content=response, view=None)

    preview = (
        f"📝 {original}\n"
        f"📅 {remind_time.strftime('%d %b %Y %H:%M')} ({tz_name})\n"
        f"🔁 Repeat: {repeat}\n\n"
        f"Konfirmasi reminder ini?"
    )
    view = ConfirmView(message.author.id, on_confirm)
    sent = await message.reply(preview, view=view)

    async def on_confirm_wrapper(interaction):
        reminder, calendar_link, calendar_error = await build_reminder(
            message.author.id, original, remind_time, repeat, tz_name
        )
        response = format_reminder_response(original, remind_time, repeat, calendar_link, calendar_error)
        await sent.edit(content=response, view=None)

    view.on_confirm = on_confirm_wrapper


# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="remind", description="Buat reminder baru")
@app_commands.describe(
    waktu="Kapan mau diingatkan, misal 'besok jam 15.00' atau 'setiap hari jam 9'",
    teks="Isi reminder",
)
async def remind_slash(interaction: discord.Interaction, waktu: str, teks: str):
    tz_name = storage.get_user_timezone(interaction.user.id)
    remind_time, repeat = parse_time(waktu, tz_name)

    preview = (
        f"📝 {teks}\n"
        f"📅 {remind_time.strftime('%d %b %Y %H:%M')} ({tz_name})\n"
        f"🔁 Repeat: {repeat}\n\n"
        f"Konfirmasi reminder ini?"
    )

    async def on_confirm(inner_interaction):
        reminder, calendar_link, calendar_error = await build_reminder(
            interaction.user.id, teks, remind_time, repeat, tz_name
        )
        response = format_reminder_response(teks, remind_time, repeat, calendar_link, calendar_error)
        await interaction.edit_original_response(content=response, view=None)

    view = ConfirmView(interaction.user.id, on_confirm)
    await interaction.response.send_message(preview, view=view)


@bot.tree.command(name="reminders", description="Lihat semua reminder aktif kamu")
async def list_reminders(interaction: discord.Interaction):
    items = storage.get_user_reminders(interaction.user.id)

    if not items:
        await interaction.response.send_message("Kamu belum punya reminder aktif.", ephemeral=True)
        return

    lines = []
    for r in items:
        t = datetime.fromisoformat(r["time"])
        repeat_tag = f" 🔁{r['repeat']}" if r["repeat"] != "none" else ""
        lines.append(f"`{r['id']}` — {t.strftime('%d %b %H:%M')}{repeat_tag} — {r['text'][:40]}")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="cancel", description="Batalkan reminder berdasarkan ID")
@app_commands.describe(reminder_id="ID reminder, bisa dilihat lewat /reminders")
async def cancel_reminder(interaction: discord.Interaction, reminder_id: str):
    items = storage.get_user_reminders(interaction.user.id)
    match = next((r for r in items if r["id"] == reminder_id), None)

    if not match:
        await interaction.response.send_message("❌ Reminder dengan ID itu tidak ditemukan.", ephemeral=True)
        return

    storage.remove_reminder(reminder_id)
    await interaction.response.send_message(f"✅ Reminder `{reminder_id}` dibatalkan.", ephemeral=True)

@bot.tree.command(name="status", description="Cek apakah server bot sedang jalan")
@app_commands.describe(reminder_id="ID Status, bisa dilihat lewat /status")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(HEALTH_CHECK_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        server_status = data.get("status", "unknown")
        bot_logged_in = data.get("bot_logged_in", False)

        if resp.status == 200 and server_status == "ok" and bot_logged_in:
            emoji = "🟢"
            summary = "Server jalan normal, bot sudah login."
        elif resp.status == 200 and server_status == "ok":
            emoji = "🟡"
            summary = "Server jalan, tapi bot belum login ke Discord."
        else:
            emoji = "🟠"
            summary = "Server merespons, tapi status tidak seperti biasanya."

        await interaction.followup.send(
            f"{emoji} **Status Server**\n"
            f"{summary}\n\n"
            f"HTTP: `{resp.status}`\n"
            f"Response: `{data}`\n"
            f"URL: {HEALTH_CHECK_URL}"
        )

    except asyncio.TimeoutError:
        await interaction.followup.send(
            f"🔴 **Server tidak merespons (timeout).**\n"
            f"URL: {HEALTH_CHECK_URL}\n"
            f"Kemungkinan server sedang down atau sangat lambat."
        )
    except Exception as e:
        await interaction.followup.send(
            f"🔴 **Gagal cek status server.**\n"
            f"Error: `{str(e)[:200]}`\n"
            f"URL: {HEALTH_CHECK_URL}"
        )


@bot.tree.command(name="remind-setting", description="Atur durasi snooze dan warna event Google Calendar kamu")
@app_commands.describe(
    snooze_menit="Durasi snooze dalam menit (contoh: 5, 10, 15)",
    warna="Warna event Google Calendar",
)
@app_commands.choices(warna=[
    app_commands.Choice(name=name.capitalize(), value=name) for name in EVENT_COLORS.keys()
])
async def remind_setting(
    interaction: discord.Interaction,
    snooze_menit: int = None,
    warna: app_commands.Choice[str] = None,
):
    updated = []

    if snooze_menit is not None:
        if snooze_menit <= 0:
            await interaction.response.send_message("❌ Snooze menit harus lebih dari 0.", ephemeral=True)
            return
        storage.set_user_snooze_minutes(interaction.user.id, snooze_menit)
        updated.append(f"⏰ Snooze diatur ke **{snooze_menit} menit**")

    if warna is not None:
        color_id = EVENT_COLORS[warna.value]
        storage.set_user_event_color(interaction.user.id, color_id)
        updated.append(f"🎨 Warna event diatur ke **{warna.name}**")

    if not updated:
        current_snooze = storage.get_user_snooze_minutes(interaction.user.id)
        current_color_id = storage.get_user_event_color(interaction.user.id)
        current_color_name = next(
            (name for name, cid in EVENT_COLORS.items() if cid == current_color_id),
            "default"
        )
        await interaction.response.send_message(
            f"Setting kamu saat ini:\n"
            f"⏰ Snooze: **{current_snooze} menit**\n"
            f"🎨 Warna event: **{current_color_name}**\n\n"
            f"Gunakan `/remind-setting snooze_menit:<angka>` dan/atau `warna:<pilihan>` untuk mengubah.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message("\n".join(updated), ephemeral=True)


@bot.tree.command(name="timezone", description="Set timezone kamu (contoh: Asia/Jakarta, Asia/Makassar)")
@app_commands.describe(tz="Nama timezone IANA, contoh: Asia/Jakarta")
async def set_timezone(interaction: discord.Interaction, tz: str):
    try:
        ZoneInfo(tz)
    except Exception:
        await interaction.response.send_message(
            "❌ Timezone tidak valid. Gunakan format IANA, contoh: `Asia/Jakarta`, `Asia/Makassar`, `Asia/Jayapura`.",
            ephemeral=True,
        )
        return

    storage.set_user_timezone(interaction.user.id, tz)
    await interaction.response.send_message(f"✅ Timezone kamu di-set ke `{tz}`.", ephemeral=True)


# =========================
# SCHEDULER
# =========================
async def reminder_loop():
    await bot.wait_until_ready()

    while True:
        now = datetime.now(timezone.utc)
        data = storage.load_reminders()
        remaining = []

        for r in data:
            t = datetime.fromisoformat(r["time"])

            if now >= t:
                user = await bot.fetch_user(r["user_id"])
                tz_name = storage.get_user_timezone(r["user_id"])
                snooze_minutes = storage.get_user_snooze_minutes(r["user_id"])

                try:
                    view = SnoozeView(r["user_id"], r["text"], tz_name, snooze_minutes)
                    await user.send(f"⏰ Reminder!\n\n📝 {r['text']}", view=view)
                except Exception as e:
                    print("Gagal kirim DM:", e)

                if r["repeat"] == "daily":
                    next_time = t + timedelta(days=1)
                    r["time"] = next_time.isoformat()
                    remaining.append(r)
                elif r["repeat"] == "weekly":
                    next_time = t + timedelta(weeks=1)
                    r["time"] = next_time.isoformat()
                    remaining.append(r)
                # kalau repeat == "none", tidak ditambahkan lagi (selesai)
            else:
                remaining.append(r)

        storage.save_reminders(remaining)
        await asyncio.sleep(10)


# =========================
# FASTAPI APP + LIFESPAN
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(bot.start(TOKEN))
    yield
    await bot.close()
    bot_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "ok", "bot_logged_in": bot.is_ready()}


@app.get("/health")
async def health():
    return {"status": "healthy"}