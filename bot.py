import os
import json
import time
import base64
import logging
from datetime import datetime

import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("task-sheet-bot")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "20"))

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TASKS_SHEET = "Задачи"
STAFF_SHEET = "Сотрудники"

# индексы столбцов на листе "Задачи" (1 = A)
COL_ID = 1
COL_TITLE = 2
COL_CREATOR = 3
COL_ASSIGNEE = 4
COL_DEADLINE = 5
COL_STATUS = 6
COL_NOTIFIED = 7
COL_TAKEN_AT = 8
COL_DONE_AT = 9

STATUS_NEW = "Новая"
STATUS_IN_PROGRESS = "В работе"
STATUS_DONE = "Выполнено"
STATUS_NOT_DONE = "Не выполнено"


def get_gspread_client():
    raw = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64)
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


# Заполняются в init_sheets() при старте. Держим как module-level переменные,
# чтобы можно было подменить в тестах без реального похода в Google.
tasks_ws = None
staff_ws = None


def init_sheets():
    global tasks_ws, staff_ws
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    tasks_ws = sh.worksheet(TASKS_SHEET)
    staff_ws = sh.worksheet(STAFF_SHEET)


def get_staff_chat_id(name):
    rows = staff_ws.get_all_records()
    for row in rows:
        if str(row.get("Имя", "")).strip() == str(name).strip():
            chat_id = row.get("Telegram Chat ID")
            if chat_id:
                return int(chat_id)
    return None


def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        r = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
        if not r.ok:
            log.warning("sendMessage failed for chat_id=%s: %s", chat_id, r.text)
            return None
        return r.json()["result"]["message_id"]
    except Exception:
        log.exception("sendMessage error for chat_id=%s", chat_id)
        return None


def edit_message(chat_id, message_id, text, buttons=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    payload["reply_markup"] = json.dumps({"inline_keyboard": buttons or []})
    try:
        r = requests.post(f"{API_URL}/editMessageText", json=payload, timeout=10)
        if not r.ok:
            log.warning("editMessageText failed: %s", r.text)
    except Exception:
        log.exception("editMessageText error")


def answer_callback(callback_id, text=None):
    try:
        requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text or ""}, timeout=10)
    except Exception:
        log.exception("answerCallbackQuery error")


def task_text(row, status_label):
    return (
        f"📌 <b>Задача:</b> {row[COL_TITLE - 1]}\n"
        f"👤 <b>Постановщик:</b> {row[COL_CREATOR - 1]}\n"
        f"⏰ <b>Срок:</b> {row[COL_DEADLINE - 1]}\n"
        f"📍 <b>Статус:</b> {status_label}"
    )


# ---------- цикл опроса таблицы: новые задачи и назначение id ----------

def poll_sheet_loop():
    while True:
        try:
            check_new_tasks()
        except Exception:
            log.exception("poll_sheet_loop error")
        time.sleep(POLL_INTERVAL)


def check_new_tasks():
    all_rows = tasks_ws.get_all_values()[1:]  # без заголовка
    changed_id = False
    used_ids = {int(r[COL_ID - 1]) for r in all_rows if str(r[COL_ID - 1]).strip().isdigit()}
    next_free_id = (max(used_ids) + 1) if used_ids else 1

    for i, row in enumerate(all_rows, start=2):  # номер строки в самой таблице
        row = row + [""] * (COL_DONE_AT - len(row))  # на случай коротких строк
        title = row[COL_TITLE - 1].strip()
        if not title:
            continue

        # присваиваем id, если его ещё нет (учитывая уже розданные в этом же проходе)
        if not str(row[COL_ID - 1]).strip():
            new_id = next_free_id
            next_free_id += 1
            tasks_ws.update_cell(i, COL_ID, new_id)
            row[COL_ID - 1] = str(new_id)
            changed_id = True

        notified = row[COL_NOTIFIED - 1].strip().lower()
        status = row[COL_STATUS - 1].strip() or STATUS_NEW

        if notified != "да":
            assignee = row[COL_ASSIGNEE - 1].strip()
            chat_id = get_staff_chat_id(assignee)
            if not chat_id:
                log.warning("Нет chat_id для исполнителя '%s' (строка %s) - жду, пока появится в листе Сотрудники", assignee, i)
                continue

            text = task_text(row, STATUS_NEW)
            buttons = [[{"text": "✅ Взял в работу", "callback_data": f"take:{row[COL_ID-1]}:{i}"}]]
            msg_id = send_message(chat_id, text, buttons)
            if msg_id:
                tasks_ws.update_cell(i, COL_NOTIFIED, "Да")
                tasks_ws.update_cell(i, COL_STATUS, status if status != STATUS_NEW else STATUS_NEW)
                # запоминаем chat_id и message_id, чтобы потом редактировать это же сообщение
                store_message_ref(i, chat_id, msg_id)

    if changed_id:
        log.info("Части задач присвоены новые id")


# храним привязку строка -> (chat_id, message_id) в памяти процесса
# (на случай перезапуска бота кнопки уже отправленных сообщений всё равно продолжат
# работать - при нажатии бот определит строку по id из callback_data)
_message_refs = {}


def store_message_ref(row_num, chat_id, msg_id):
    _message_refs[row_num] = (chat_id, msg_id)


# ---------- обработка нажатий на кнопки ----------

def find_row_by_id(task_id):
    all_rows = tasks_ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if str(row[COL_ID - 1]).strip() == str(task_id):
            return i, row
    return None, None


def handle_callback(update):
    cq = update["callback_query"]
    data = cq["data"]  # формат "action:task_id:row_hint"
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]

    parts = data.split(":")
    action = parts[0]
    task_id = parts[1]

    row_num, row = find_row_by_id(task_id)
    if row_num is None:
        answer_callback(cq["id"], "Задача не найдена (возможно, удалена из таблицы)")
        return

    row = row + [""] * (COL_DONE_AT - len(row))
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    if action == "take":
        tasks_ws.update_cell(row_num, COL_STATUS, STATUS_IN_PROGRESS)
        tasks_ws.update_cell(row_num, COL_TAKEN_AT, now)
        buttons = [
            [
                {"text": "✅ Выполнено", "callback_data": f"done:{task_id}:{row_num}"},
                {"text": "❌ Не выполнено", "callback_data": f"notdone:{task_id}:{row_num}"},
            ]
        ]
        edit_message(chat_id, message_id, task_text(row, STATUS_IN_PROGRESS), buttons)
        answer_callback(cq["id"], "Взяли в работу")

    elif action == "done":
        tasks_ws.update_cell(row_num, COL_STATUS, STATUS_DONE)
        tasks_ws.update_cell(row_num, COL_DONE_AT, now)
        edit_message(chat_id, message_id, task_text(row, STATUS_DONE), buttons=None)
        answer_callback(cq["id"], "Отмечено как выполнено")

    elif action == "notdone":
        tasks_ws.update_cell(row_num, COL_STATUS, STATUS_NOT_DONE)
        tasks_ws.update_cell(row_num, COL_DONE_AT, now)
        edit_message(chat_id, message_id, task_text(row, STATUS_NOT_DONE), buttons=None)
        answer_callback(cq["id"], "Отмечено как не выполнено")


# ---------- обработка обычных сообщений (/start) ----------

def handle_message(update):
    msg = update["message"]
    if msg.get("text", "").strip() == "/start":
        chat_id = msg["chat"]["id"]
        send_message(chat_id, f"Твой telegram chat_id: {chat_id}\nПопроси того, кто ведёт таблицу, вписать его в лист «Сотрудники» напротив твоего имени.")


def telegram_poll_loop():
    offset = 0
    while True:
        try:
            r = requests.get(f"{API_URL}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40)
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    handle_callback(upd)
                elif "message" in upd:
                    handle_message(upd)
        except Exception:
            log.exception("telegram_poll_loop error")
            time.sleep(5)


def main():
    init_sheets()
    log.info("Бот запущен, читаю таблицу %s", SPREADSHEET_ID)
    import threading
    t = threading.Thread(target=poll_sheet_loop, daemon=True)
    t.start()
    telegram_poll_loop()


if __name__ == "__main__":
    main()
