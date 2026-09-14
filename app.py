import os
import json
import logging

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("task-bot")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "changeme")
# URL твоего рабочего кабинета KeyCRM (тот, что в адресной строке когда ты залогинена) - подставь свой
KEYCRM_URL = os.getenv("KEYCRM_URL", "https://ЗАМЕНИ-НА-СВОЙ-АДРЕС.keycrm.app")

# 1. КОМУ ОТПРАВЛЯЕМ: id ответственного в KeyCRM -> его chat_id в телеграме (число, не строка)
USER_CHAT_MAPPING = {
    11: 123456789,   # Артем: замени 11 на его id в KeyCRM, 123456789 - на его chat_id
    22: 987654321,   # Марина: аналогично
}

# 2. Имена постановщиков для текста сообщения: id в KeyCRM -> имя
CREATOR_MAPPING = {
    11: "Артем",
    22: "Марина",
}

# Куда слать, если исполнителя нет в USER_CHAT_MAPPING (например общая группа) - необязательно
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram_message(text, chat_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            log.warning("Telegram sendMessage failed for chat_id=%s: %s", chat_id, r.text)
    except Exception:
        log.exception("Ошибка отправки в Telegram для chat_id=%s", chat_id)


def extract_task_event(payload):
    """
    Достаём тип события и данные задачи из вебхука KeyCRM.
    Название события ('task.created' и т.п.) и структура payload не подтверждены
    официальной документацией KeyCRM - там описаны только события для заказов и
    карточек воронки. Поэтому здесь проверяется несколько вероятных вариантов
    расположения полей, а сырой payload всегда пишется в лог - если ни один
    вариант не совпал, смотри лог и правь эту функцию под реальную структуру.
    """
    event = payload.get("event") or payload.get("context", {}).get("event") or ""

    # данные задачи могут лежать в разных местах в зависимости от реальной структуры
    task = (
        payload.get("context")
        if isinstance(payload.get("context"), dict) and "id" in payload.get("context", {})
        else payload.get("data")
        if isinstance(payload.get("data"), dict)
        else payload
    )

    task_id = task.get("id", "—")
    title = task.get("title") or task.get("name") or task.get("task") or "Без названия"
    worker_id = task.get("worker_id") or task.get("responsible_id") or task.get("assignee_id")
    creator_id = task.get("creator_id") or task.get("author_id") or task.get("created_by")

    return event, task_id, title, worker_id, creator_id


@app.route("/webhook", methods=["POST"])
def keycrm_webhook():
    if request.args.get("secret") != WEBHOOK_SECRET:
        return jsonify({"status": "error", "message": "bad secret"}), 403

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "message": "No data received"}), 400

    log.info("KeyCRM webhook raw payload: %s", json.dumps(payload, ensure_ascii=False))

    event, task_id, title, worker_id, creator_id = extract_task_event(payload)

    if event != "task.created":
        # логируем и выходим - это либо не то событие, либо название совпадает не так,
        # как мы предположили (см. лог выше)
        log.info("Событие '%s' не обрабатываем (ждём task.created)", event)
        return jsonify({"status": "ok", "skipped": True}), 200

    target_chat_id = USER_CHAT_MAPPING.get(worker_id, DEFAULT_CHAT_ID)
    creator_name = CREATOR_MAPPING.get(creator_id, "Кто-то из команды")

    if not target_chat_id:
        log.warning("Нет chat_id для worker_id=%s и нет DEFAULT_CHAT_ID - уведомление не отправлено", worker_id)
        return jsonify({"status": "ok", "notified": False}), 200

    text = (
        f"👋 <b>Вам назначена новая задача</b>\n\n"
        f"👤 <b>Постановщик:</b> {creator_name}\n"
        f"📝 <b>Задача:</b> {title}\n\n"
        f"👉 <a href='{KEYCRM_URL}/tasks/{task_id}'>Открыть задачу</a>"
    )
    send_telegram_message(text, target_chat_id)

    return jsonify({"status": "ok", "notified": True}), 200


@app.route("/", methods=["GET"])
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
