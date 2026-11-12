import os
import requests
from flask import Flask, request

app = Flask(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

@app.route("/", methods=["GET"])
def home():
    return "Vozduh bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if chat_id:
        if text == "/start":
            reply = (
                "Привет! 🎈 Это магазин «Воздух».\n"
                "Поможем выбрать воздушные шары!\n"
                "Телефон: +7 901 901-08-82"
            )
        else:
            reply = "Спасибо за сообщение! 🎈 Скоро ответим."

        requests.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": reply},
            timeout=20
        )

    return "OK", 200

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
