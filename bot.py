import os
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

DB = os.getenv("DB_PATH", "howmanydays.db")
TZ = ZoneInfo("Asia/Singapore")

def today():
    return datetime.now(TZ).date()

def connect_db():
    conn = sqlite3.connect(DB)
    return conn

def init_db():
    with connect_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                chat_id INTEGER,
                name TEXT,
                start_date TEXT,
                end_date TEXT,
                PRIMARY KEY (chat_id, name)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS keywords (
                chat_id INTEGER,
                keyword TEXT,
                PRIMARY KEY (chat_id, keyword)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS mentions (
                chat_id INTEGER,
                keyword TEXT,
                username TEXT,
                mention_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, keyword, username)
            )
            """
        )

        columns = [
            row[1]
            for row in db.execute("PRAGMA table_info(mentions)")
        ]

        if "last_mentioned" not in columns:
            db.execute("""
                ALTER TABLE mentions
                ADD COLUMN last_mentioned TEXT
            """)

async def send(update, message):
    await update.effective_message.reply_text(message)

async def addevent(update, context):
    if len(context.args) < 3:
        return await send(update, "Usage: /addevent <name> <DD-MM-YYYY> <DD-MM-YYYY>")

    name = " ".join(context.args[:-2]).lower()
    start, end = context.args[-2:]

    try:
        start_date = datetime.strptime(start, "%d-%m-%Y").date()
        end_date = datetime.strptime(end, "%d-%m-%Y").date()
    except ValueError:
        return await send(update, "Invalid date. Please use DD-MM-YYYY.")

    if start_date > end_date:
        return await send(update, "Start date cannot be after end date.")

    with connect_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?)
            """,
            (update.effective_chat.id, name, start_date.isoformat(), end_date.isoformat()),
        )

    await send(update, f"Event '{name}' added!")

async def event_days(update, context, mode):
    if not context.args:
        return await send(update, "Please specify an event.")

    name = " ".join(context.args).lower()

    with connect_db() as db:
        result = db.execute(
            """
            SELECT start_date, end_date FROM events
            WHERE chat_id = ? AND name = ?
            """,
            (update.effective_chat.id, name),
        ).fetchone()

    if not result:
        return await send(update, f"Event '{name}' not found.")

    start, end = map(lambda d: datetime.fromisoformat(d).date(), result)
    current = today()

    if mode == "to":
        days = (start - current).days
        message = (
            f"{days} days until {name}"
            if days >= 0 else
            f"{name} started {-days} days ago"
        )
    else:
        days = (current - end).days
        message = (
            f"{days} days since {name}"
            if days >= 0 else
            f"{name} ends in {-days} days"
        )
    await send(update, message)

async def howmanydaysto(update, context):
    await event_days(update, context, "to")

async def howmanydayssince(update, context):
    if not context.args:
        return await send(update, "Usage: /howmanydayssince <keyword>")

    keyword = " ".join(context.args).lower()
    chat_id = update.effective_chat.id

    with connect_db() as db:
        exists = db.execute(
            """
            SELECT 1 FROM keywords 
            WHERE chat_id = ? AND keyword = ?
        """, (chat_id, keyword)).fetchone()

        if not exists:
            return await send(
                update, f"'{keyword}' not found."
            )

async def addkeyword(update, context):
    if len(context.args) < 1:
        return await send(update, "Usage: /addkeyword <keyword>")

    keyword = " ".join(context.args).lower()
    chat_id = update.effective_chat.id

    with connect_db() as db:
        result = db.execute(
            """
            INSERT OR IGNORE INTO keywords VALUES (?, ?)
            """,
            (chat_id, keyword),
        )

    if result.rowcount == 0:
        await send(update, f"'{keyword}' already exists")
    else:
        await send(update, f"Now tracking: '{keyword}'")

async def zerodayssince(update, context):
    if len(context.args) < 2:
        return await send(update, "Usage: /zerodayssince <keyword> @username")

    keyword = " ".join(context.args[:-1]).lower()
    username = context.args[-1]

    if not username.startswith("@"):
        return await send(update, "Please mention a user with @username")

    username = username[1:]  # Remove the '@'
    chat_id = update.effective_chat.id

    with connect_db() as db:
        exists = db.execute(
            "SELECT 1 FROM keywords WHERE chat_id = ? AND keyword = ?",
            (chat_id, keyword),
        ).fetchone()

        if not exists:
            return await send(update, f"{keyword} not found")

        db.execute(
            """
            INSERT INTO mentions (
                chat_id, keyword, username, 
                mention_count, last_mentioned
            )
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(chat_id, keyword, username)
            DO UPDATE SET
                mention_count = mentions.mention_count + 1,
                last_mentioned = excluded.last_mentioned
            """,
            (
                chat_id,
                keyword,
                username,
                today().isoformat()
            )
        )

        count = db.execute(
            """
            SELECT mention_count FROM mentions
            WHERE chat_id = ? AND keyword = ? AND username = ?
            """,
            (chat_id, keyword, username),
        ).fetchone()[0]
    
    await send(
        update, 
        f"0 days since @{username} mentioned '{keyword}'"
        f" (Total mentions: {count})"
    )

async def viewall(update, context):
    chat_id = update.effective_chat.id

    with connect_db() as db:
        events = db.execute("""
            SELECT name, start_date, end_date
            FROM events
            WHERE chat_id = ?
            ORDER BY start_date
        """, (chat_id,)).fetchall()

        keywords = db.execute("""
            SELECT keyword
            FROM keywords
            WHERE chat_id = ?
            ORDER BY keyword
        """, (chat_id,)).fetchall()

    lines = ["EVENTS"]
    if events:
        for name, start, end in events:
            start = date.fromisoformat(start).strftime("%d-%m-%Y")
            end = date.fromisoformat(end).strftime("%d-%m-%Y")
            lines.append(f"• {name}: {start} → {end}")
    else:
        lines.append("No events added.")

    lines.append("\nKEYWORDS")
    if keywords:
        for (keyword,) in keywords:
            lines.append(f"• {keyword}")
    else:
        lines.append("No keywords added.")

    await send(update, "\n".join(lines))

async def deleteevent(update, context):
    if not context.args:
        return await send(update, "Usage: /deleteevent <event name>")

    name = " ".join(context.args).lower()
    chat_id = update.effective_chat.id

    with connect_db() as db:
        result = db.execute("""
            DELETE FROM events
            WHERE chat_id = ? AND name = ?
        """, (chat_id, name))

    if result.rowcount == 0:
        await send(update, f"'{name}' not found.")
    else:
        await send(update, f"'{name}' deleted.")

async def deletekeyword(update, context):
    if not context.args:
        return await send(update, "Usage: /deletekeyword <keyword>")

    keyword = " ".join(context.args).lower()
    chat_id = update.effective_chat.id

    with connect_db() as db:
        result = db.execute("""
            DELETE FROM keywords
            WHERE chat_id = ? AND keyword = ?
        """, (chat_id, keyword))

        if result.rowcount > 0:
            db.execute("""
                DELETE FROM mentions
                WHERE chat_id = ? AND keyword = ?
            """, (chat_id, keyword))

    if result.rowcount == 0:
        await send(update, f"'{keyword}' not found.")
    else:
        await send(
            update,
            f"'{keyword}' and its leaderboard deleted."
        )

async def leaderboard(update, context):
    if not context.args:
        return await send(update, "Usage: /leaderboard <keyword>")

    keyword = " ".join(context.args).lower()
    chat_id = update.effective_chat.id

    with connect_db() as db:
        exists = db.execute(
            "SELECT 1 FROM keywords WHERE chat_id = ? AND keyword = ?",
            (chat_id, keyword),
        ).fetchone()

        if not exists:
            return await send(update, f"'{keyword}' not found")

        rows = db.execute(
            """
            SELECT username, mention_count 
            FROM mentions
            WHERE chat_id = ? AND keyword = ?
            ORDER BY mention_count DESC, username ASC
            """,
            (chat_id, keyword),
        ).fetchall()

        if not rows:
            return await send(update, f"No mentions recorded for {keyword}")

        lines = [f"🏆 {keyword.upper()} Leaderboard\n"]
        medals = ["🥇", "🥈", "🥉"]

        for i, (username, count) in enumerate(rows):
            rank = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{rank} @{username}: {count} mentions")

        await send(update, "\n".join(lines))

async def start(update, context):
    print("Received /start command!")
    await update.message.reply_text("Hello! HowManyDaysBot is working!")

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    init_db()

    app = ApplicationBuilder().token(token).build()

    handlers = {
        "start": start,
        "addevent": addevent,
        "howmanydaysto": howmanydaysto,
        "howmanydayssince": howmanydayssince,
        "addkeyword": addkeyword,
        "zerodayssince": zerodayssince,
        "leaderboard": leaderboard,
        "viewall": viewall,
        "deleteevent": deleteevent,
        "deletekeyword": deletekeyword,
    }

    for command, handler in handlers.items():
        app.add_handler(CommandHandler(command, handler))

    print("Starting HowManyDays bot...", flush=True)
    app.run_polling()

if __name__ == "__main__":
    main()
