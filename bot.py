import os
import json
import time
import sqlite3
import logging
import threading

import requests
from flask import Flask, request, jsonify

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_TELEGRAM_ID"])
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme")
DB_PATH = os.environ.get("DB_PATH", "assign_bot.db")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("assign-bot")

app = Flask(__name__)


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS managers (
            manager_id INTEGER PRIMARY KEY,
            telegram_chat_id INTEGER NOT NULL,
            telegram_username TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cards (
            card_id INTEGER PRIMARY KEY,
            last_manager_id INTEGER
        )"""
    )
    conn.commit()
    return conn


conn = get_conn()
db_lock = threading.Lock()


def send_message(chat_id, text):
    try:
        r = requests.post(f"{API_URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
        if not r.ok:
            log.warning("sendMessage failed for chat_id=%s: %s", chat_id, r.text)
    except Exception:
        log.exception("sendMessage error for chat_id=%s", chat_id)


# ---------- Telegram: long polling для команд ----------

def telegram_poll_loop():
    offset = 0
    while True:
        try:
            r = requests.get(f"{API_URL}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40)
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                handle_update(upd)
        except Exception:
            log.exception("polling error")
            time.sleep(5)


def handle_update(upd):
    msg = upd.get("message")
    if not msg or "text" not in msg:
        return

    chat_id = msg["chat"]["id"]
    text = msg["text"].strip()

    if text == "/start":
        send_message(
            chat_id,
            f"Твой telegram chat_id: {chat_id}\n"
            f"Попроси админа привязать тебя командой /link <manager_id из KeyCRM> {chat_id}",
        )
        return

    # остальные команды - только админ
    if chat_id != ADMIN_ID:
        return

    if text.startswith("/link"):
        parts = text.split()
        if len(parts) != 3:
            send_message(chat_id, "Формат: /link <manager_id> <telegram_chat_id>")
            return
        try:
            manager_id, tg_id = int(parts[1]), int(parts[2])
        except ValueError:
            send_message(chat_id, "manager_id и telegram_chat_id должны быть числами")
            return
        with db_lock:
            conn.execute(
                "INSERT INTO managers (manager_id, telegram_chat_id) VALUES (?, ?) "
                "ON CONFLICT(manager_id) DO UPDATE SET telegram_chat_id=excluded.telegram_chat_id",
                (manager_id, tg_id),
            )
            conn.commit()
        send_message(chat_id, f"Привязано: manager_id {manager_id} -> chat_id {tg_id}")
        return

    if text.startswith("/unlink"):
        parts = text.split()
        if len(parts) != 2:
            send_message(chat_id, "Формат: /unlink <manager_id>")
            return
        with db_lock:
            conn.execute("DELETE FROM managers WHERE manager_id=?", (int(parts[1]),))
            conn.commit()
        send_message(chat_id, "Отвязано")
        return

    if text == "/list":
        rows = conn.execute("SELECT manager_id, telegram_chat_id FROM managers").fetchall()
        if not rows:
            send_message(chat_id, "Пока никто не привязан")
        else:
            send_message(chat_id, "\n".join(f"{m} -> {c}" for m, c in rows))
        return

    if text == "/help":
        send_message(
            chat_id,
            "/link <manager_id> <telegram_chat_id> - привязать менеджера\n"
            "/unlink <manager_id> - отвязать\n"
            "/list - показать все привязки",
        )
        return


# ---------- KeyCRM: входящий вебхук ----------

@app.route("/keycrm-webhook", methods=["POST"])
def keycrm_webhook():
    if request.args.get("secret") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "bad secret"}), 403

    payload = request.get_json(silent=True) or {}
    log.info("KeyCRM webhook raw payload: %s", json.dumps(payload, ensure_ascii=False))

    if payload.get("event") != "lead.change_lead_status":
        return jsonify({"ok": True}), 200

    context = payload.get("context") or {}
    card_id = context.get("id")
    manager_id = context.get("manager_id")

    if card_id is None or manager_id is None:
        log.warning("В payload нет id или manager_id: %s", context)
        return jsonify({"ok": True}), 200

    with db_lock:
        row = conn.execute("SELECT last_manager_id FROM cards WHERE card_id=?", (card_id,)).fetchone()
        prev_manager = row[0] if row else None

        conn.execute(
            "INSERT INTO cards (card_id, last_manager_id) VALUES (?, ?) "
            "ON CONFLICT(card_id) DO UPDATE SET last_manager_id=excluded.last_manager_id",
            (card_id, manager_id),
        )
        conn.commit()

    if prev_manager != manager_id:
        mrow = conn.execute("SELECT telegram_chat_id FROM managers WHERE manager_id=?", (manager_id,)).fetchone()
        if mrow:
            title = context.get("title") or ""
            send_message(mrow[0], f"Тебе назначена карточка #{card_id} «{title}» в KeyCRM")
        else:
            log.info("Менеджер %s не привязан к telegram - уведомление не отправлено", manager_id)

    return jsonify({"ok": True}), 200


@app.route("/", methods=["GET"])
def health():
    return "ok", 200


# Стартуем поллинг телеграма при импорте модуля (важно и для gunicorn, и для python bot.py)
_poll_thread = threading.Thread(target=telegram_poll_loop, daemon=True)
_poll_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
