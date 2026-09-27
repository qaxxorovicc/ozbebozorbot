import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession


# =========================
# SOZLAMALAR
# =========================

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL", "")
DESTINATION_CHANNEL = os.getenv("DESTINATION_CHANNEL", "")

TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

MY_LINK = "https://t.me/+aUi-3h501IsyYzAy"

MY_FOOTER = """Eslatma📌
mashina uchun oldindan tolov qilmang. Savdoga admin javobgar emas❗️❗️❗️"""

DB_FILE = "queue.db"


# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_ids TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    return conn


def add_to_queue(message_ids):
    conn = db()

    conn.execute(
        """
        INSERT INTO queue(message_ids, created_at)
        VALUES (?, ?)
        """,
        (
            json.dumps(message_ids),
            datetime.now().isoformat()
        )
    )

    conn.commit()
    conn.close()


def get_next():
    conn = db()

    row = conn.execute(
        """
        SELECT id, message_ids
        FROM queue
        ORDER BY id ASC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "message_ids": json.loads(row[1])
    }


def delete_queue(queue_id):
    conn = db()

    conn.execute(
        "DELETE FROM queue WHERE id = ?",
        (queue_id,)
    )

    conn.commit()
    conn.close()


def queue_count():
    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM queue"
    ).fetchone()[0]

    conn.close()

    return count


# =========================
# LINK ALMASHTIRISH
# =========================

URL_PATTERN = re.compile(
    r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+)",
    re.IGNORECASE
)


def process_text(text):

    if not text:
        return text

    matches = list(URL_PATTERN.finditer(text))

    # Link yo'q bo'lsa — umuman o'zgartirmaymiz
    if not matches:
        return text

    # Oxirgi linkdan oldingi qismni saqlaymiz
    last_link = matches[-1]

    before_link = text[:last_link.start()].rstrip()

    return (
        before_link
        + "\n\n"
        + MY_LINK
        + "\n\n"
        + MY_FOOTER
    )


# =========================
# TELEGRAM
# =========================

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)


# =========================
# YANGI POSTNI QABUL QILISH
# =========================

@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def new_post(event):

    message = event.message

    # Album bo'lsa:
    # keyinroq album event orqali yig'iladi.
    if message.grouped_id:
        return

    add_to_queue([message.id])

    print(
        f"Yangi post navbatga qo'shildi: "
        f"{message.id} | Navbat: {queue_count()}"
    )


# =========================
# ALBUM
# =========================

@client.on(events.Album(chats=SOURCE_CHANNEL))
async def new_album(event):

    ids = [m.id for m in event.messages]

    if not ids:
        return

    add_to_queue(ids)

    print(
        f"Album navbatga qo'shildi: "
        f"{ids} | Navbat: {queue_count()}"
    )


# =========================
# POSTNI KANALGA CHIQARISH
# =========================

async def publish_next():

    item = get_next()

    if not item:
        print("Navbat bo'sh.")
        return

    queue_id = item["id"]
    message_ids = item["message_ids"]

    try:

        messages = await client.get_messages(
            SOURCE_CHANNEL,
            ids=message_ids
        )

        messages = [
            m for m in messages
            if m is not None
        ]

        if not messages:
            delete_queue(queue_id)
            return

        # =====================
        # ALBUM
        # =====================

        if len(messages) > 1:

            media = []

            caption = None

            for index, message in enumerate(messages):

                if message.media:

                    media.append(message.media)

                if index == 0:

                    caption = (
                        message.message
                        if message.message
                        else None
                    )

            caption = process_text(caption)

            if media:

                await client.send_file(
                    DESTINATION_CHANNEL,
                    media,
                    caption=caption
                )

        # =====================
        # ODDIY POST
        # =====================

        else:

            message = messages[0]

            text = message.message

            new_text = process_text(text)

            # Rasm/video/document
            if message.media:

                await client.send_file(
                    DESTINATION_CHANNEL,
                    message.media,
                    caption=new_text
                )

            # Oddiy text
            else:

                await client.send_message(
                    DESTINATION_CHANNEL,
                    new_text
                )

        delete_queue(queue_id)

        print(
            f"Post kanalga chiqarildi: "
            f"{message_ids} | Qolgan: {queue_count()}"
        )

    except Exception as e:

        print(
            "POST YUBORISHDA XATO:",
            repr(e)
        )

        # Xato bo'lsa navbatdan o'chirmaymiz.
        # Keyingi :59 da yana urinadi.


# =========================
# HAR SOAT :59
# =========================

async def scheduler():

    last_slot = None

    tz = ZoneInfo(TIMEZONE)

    while True:

        now = datetime.now(tz)

        slot = now.strftime(
            "%Y-%m-%d %H:%M"
        )

        if now.minute == 59:

            if slot != last_slot:

                last_slot = slot

                print(
                    f"{slot} — scheduler ishladi."
                )

                await publish_next()

        await asyncio.sleep(1)


# =========================
# HEALTH CHECK
# =========================

async def health(request):

    return web.Response(
        text="OZBEBOZOR bot ishlayapti."
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    port = int(
        os.getenv("PORT", "10000")
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"Health server: {port}"
    )


# =========================
# MAIN
# =========================

async def main():

    print("OZBEBOZOR bot ishga tushmoqda...")

    await start_web_server()

    await client.start()

    me = await client.get_me()

    print(
        "Telegram akkaunt:",
        me.first_name,
        me.id
    )

    print(
        "Source:",
        SOURCE_CHANNEL
    )

    print(
        "Destination:",
        DESTINATION_CHANNEL
    )

    print(
        "Queue:",
        queue_count()
    )

    await asyncio.gather(
        client.run_until_disconnected(),
        scheduler()
    )


if __name__ == "__main__":

    asyncio.run(main())
