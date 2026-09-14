import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Токены из переменных окружения Railway
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
KEYCRM_URL = os.getenv("KEYCRM_URL", "https://crm.keycrm.app")

# 1. СЛОВАРЬ ОТВЕТСТВЕННЫХ (КОМУ ОТПРАВЛЯЕМ)
# Слева — ID сотрудника в KeyCRM, справа — его личный Chat ID в Telegram
USER_CHAT_MAPPING = {
    11: "ID_ТЕЛЕГРАМ_АРТЕМА",  # Замените 11 на ваш ID в KeyCRM, а строку на ваш Chat ID
    22: "ID_ТЕЛЕГРАМ_МАРИНЫ"   # Замените 22 на ID Марины в KeyCRM, а строку на её Chat ID
}

# 2. СЛОВАРЬ ПОСТАНОВЩИКОВ (КТО ПОСТАВИЛ ЗАДАЧУ)
# Слева — ID сотрудника в KeyCRM, справа — его имя
CREATOR_MAPPING = {
    11: "Артем",
    22: "Марина" 
}

# Чат по умолчанию (если worker_id нет в словаре, например, общая группа)
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") 

def send_telegram_message(text, chat_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

@app.route('/webhook', methods=['POST'])
def keycrm_webhook():
    data = request.json
    
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    event = data.get('context', {}).get('event', '')
    
    if event == 'task.created':
        task_data = data.get('data', {})
        
        task_id = task_data.get('id', '—')
        task_title = task_data.get('title', 'Без названия')
        
        # Получаем ID ответственного и постановщика
        worker_id = task_data.get('worker_id') 
        creator_id = task_data.get('creator_id')

        # Определяем, куда отправлять сообщение
        target_chat_id = USER_CHAT_MAPPING.get(worker_id, DEFAULT_CHAT_ID)
        
        # Определяем имя постановщика
        creator_name = CREATOR_MAPPING.get(creator_id, "Кто-то из команды")
        
        if target_chat_id:
            # Формируем персонализированный текст
            text = (
                f"👋 <b>Добрый день! Вам назначена новая задача</b>\n\n"
                f"👤 <b>Постановщик:</b> {creator_name}\n"
                f"📝 <b>Задача:</b> {task_title}\n\n"
                f"👉 <a href='{KEYCRM_URL}/tasks/{task_id}'>Открыть задачу</a>"
            )
            send_telegram_message(text, target_chat_id)

    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)