import hmac
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone, timedelta

import requests
from flask import Flask, request

app = Flask(__name__)
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
OWNER = int(os.getenv("BOOKING_OWNER_ID", "0"))
PRICE = os.getenv("BOOKING_PRICE", "")
DATES = sorted({date.fromisoformat(day.strip()).isoformat()
                for day in os.getenv("BOOKING_DATES", "").split(",") if day.strip()})
DATABASE = os.getenv("BOOKING_DATABASE", "bookings.sqlite3")
MOSCOW = timezone(timedelta(hours=3))
if OWNER and not SECRET:
    raise RuntimeError("Set TELEGRAM_WEBHOOK_SECRET before enabling bookings")


def telegram(method, **payload):
    try:
        response = requests.post(f"{API}/{method}", json=payload, timeout=8)
        if response.ok and response.json().get("ok"):
            return
    except (requests.RequestException, ValueError):
        pass
    raise RuntimeError(f"Telegram request failed: {method}")


def send(chat, text, **options):
    telegram("sendMessage", chat_id=chat, text=text, **options)


def keyboard(*buttons):
    return {"reply_markup": {"inline_keyboard": [
        [{"text": label, "callback_data": action}] for label, action in buttons
    ]}}


def connect():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS forms (chat INTEGER PRIMARY KEY, day TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY, chat INTEGER NOT NULL, day TEXT NOT NULL,
            details TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS confirmed_day ON bookings(day)
            WHERE status = 'confirmed';
        CREATE TABLE IF NOT EXISTS processed (id INTEGER PRIMARY KEY);
    """)
    return db


def today():
    return datetime.now(MOSCOW).date().isoformat()


def available(db):
    taken = {row[0] for row in db.execute(
        "SELECT day FROM bookings WHERE status = 'confirmed'")}
    return [day for day in DATES if day >= today() and day not in taken]


def handle_message(db, message):
    chat = message["chat"]["id"]
    text = message.get("text", "").strip()
    command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
    if command == "/start":
        options = keyboard(("Выбрать дату", "dates")) if OWNER and PRICE else {}
        send(chat, "Привет! 🎈 Это магазин «Воздух».\n"
                   "Поможем выбрать воздушные шары!\n"
                   "Телефон: +7 901 901-08-82", **options)
    elif command == "/cancel" and OWNER:
        db.execute("DELETE FROM forms WHERE chat = ?", (chat,))
        send(chat, "Заполнение анкеты отменено. Нажмите /start, чтобы начать заново.")
    elif OWNER and (form := db.execute("SELECT day FROM forms WHERE chat = ?", (chat,)).fetchone()):
        day = form[0]
        if not 5 <= len(text) <= 1500 or command.startswith("/"):
            send(chat, "Напишите одним сообщением ваше имя, контакт и какие шары нужны.")
            return
        db.execute("DELETE FROM forms WHERE chat = ?", (chat,))
        if day not in available(db):
            send(chat, "Дата уже недоступна. Выберите другую.", **keyboard(("Другие даты", "dates")))
            return
        booking = db.execute(
            "INSERT INTO bookings(chat, day, details) VALUES (?, ?, ?)", (chat, day, text)
        ).lastrowid
        send(OWNER, f"Заявка №{booking}\nДата: {day}\n{text}", **keyboard(
            ("Подтвердить", f"confirm:{booking}"), ("Отклонить", f"reject:{booking}")))
        send(chat, f"Заявка №{booking} на {day} отправлена. Дождитесь ответа магазина.")
    else:
        send(chat, "Спасибо за сообщение! 🎈 Скоро ответим.")


def handle_callback(db, callback):
    chat = callback["from"]["id"]
    action, _, value = callback.get("data", "").partition(":")
    telegram("answerCallbackQuery", callback_query_id=callback["id"])
    if not OWNER or not PRICE or callback.get("message", {}).get("chat", {}).get("id") != chat:
        return
    if action == "dates":
        days = available(db)[:30]
        if days:
            send(chat, f"Стоимость: {PRICE}\nВыберите свободную дату.", **keyboard(*[
                (day, f"day:{day}") for day in days]))
        else:
            send(chat, "Сейчас свободных дат нет. Напишите нам, чтобы уточнить наличие.")
    elif action == "day":
        if value not in available(db):
            send(chat, "Дата уже недоступна.", **keyboard(("Другие даты", "dates")))
            return
        db.execute("INSERT OR REPLACE INTO forms VALUES (?, ?)", (chat, value))
        send(chat, f"Дата: {value}\nСтоимость: {PRICE}\n"
                   "Напишите одним сообщением ваше имя, контакт и какие шары нужны. "
                   "Для отмены анкеты нажмите /cancel.")
    elif chat == OWNER and action in {"confirm", "reject"} and value.isascii() and value.isdigit() and len(value) < 19:
        row = db.execute(
            "SELECT chat, day FROM bookings WHERE id = ? AND status = 'pending'", (int(value),)
        ).fetchone()
        if row is None:
            send(chat, "Заявка уже обработана или не найдена.")
            return
        customer, day = row
        if action == "confirm" and day not in available(db):
            send(chat, "Дата недоступна. Заявку всё ещё можно отклонить.")
            return
        status = "confirmed" if action == "confirm" else "rejected"
        db.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, int(value)))
        result = "подтверждена" if action == "confirm" else "отклонена"
        send(customer, f"Заявка №{value} на {day} {result}.")
        send(chat, f"Заявка №{value} {result}.")


@app.get("/")
def home():
    return "Vozduh bot is running!"


@app.post("/webhook")
def webhook():
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if SECRET and not hmac.compare_digest(SECRET.encode(), supplied.encode()):
        return "Forbidden", 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return "Bad Request", 400
    message = data.get("message", {})
    callback = data.get("callback_query")
    if not callback and message.get("chat", {}).get("type") != "private":
        return "OK", 200
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        update_id = data.get("update_id")
        if isinstance(update_id, int) and not db.execute(
            "INSERT OR IGNORE INTO processed VALUES (?)", (update_id,)
        ).rowcount:
            return "OK", 200
        if callback:
            handle_callback(db, callback)
        else:
            handle_message(db, message)
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
