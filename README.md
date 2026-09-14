# zevs-assign-bot

Телеграм-бот, который пишет менеджеру ZEVS PARTS лично, когда на него в KeyCRM назначают карточку в воронке.

## Как это работает

KeyCRM умеет только отправлять исходящие вебхуки (принимать команды через API нельзя, только настроить в интерфейсе). Бот поднимает веб-сервер на Railway с адресом `/keycrm-webhook`. В KeyCRM настраивается триггер: при смене статуса карточки воронки (`lead.change_lead_status`) отправлять POST на этот адрес. Бот сравнивает `manager_id` из вебхука с тем, что было раньше по этой карточке, и если ответственный сменился - шлёт привязанному к этому manager_id телеграм-чату сообщение.

Формат вебхука от KeyCRM проверен по их официальной документации (help.keycrm.app) на сегодня. Если KeyCRM поменяет структуру - в логах Railway всегда пишется сырой JSON вебхука (`KeyCRM webhook raw payload`), по нему можно поправить код.

## Переменные окружения

- `TELEGRAM_BOT_TOKEN` - токен бота от @BotFather
- `ADMIN_TELEGRAM_ID` - твой telegram chat_id, только у тебя будут работать команды /link, /unlink, /list
- `WEBHOOK_SECRET` - произвольная длинная строка, защищает вебхук от чужих запросов
- `DB_PATH` - необязательно, путь к файлу sqlite (по умолчанию assign_bot.db)

Пример - в .env.example.

## Деплой на Railway

1. Создать новый проект на Railway, подключить этот репозиторий.
2. В Variables добавить `TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `WEBHOOK_SECRET`.
3. Railway сам распознает Procfile и requrements.txt, задеплоит.
4. Важно: sqlite-файл лежит на диске контейнера, при пересборке без volume данные о привязках менеджеров теряются. Если это критично - подключить Railway Volume и указать его путь в `DB_PATH`.
5. После деплоя скопировать публичный домен Railway (Settings -> Networking -> Generate Domain).

## Настройка в KeyCRM

В воронке (той, где назначаются заявки): Автоматизация -> триггер на смену статуса карточки (можно на любой статус, событие сработает при любой смене) -> действие "Відправити Webhook":
- Метод: POST
- URL: `https://<домен-с-railway>/keycrm-webhook?secret=<значение WEBHOOK_SECRET>`

## Привязка менеджеров к телеграму

1. Каждый менеджер пишет боту `/start` - бот присылает его chat_id.
2. Админ (тот, чей telegram id указан в ADMIN_TELEGRAM_ID) шлёт боту `/link <manager_id из KeyCRM> <chat_id менеджера>`.
3. `/list` - посмотреть все текущие привязки, `/unlink <manager_id>` - убрать.

manager_id менеджера в KeyCRM видно в самой карточке сотрудника или в адресной строке при открытии его профиля в настройках KeyCRM.

## Локальный запуск

```
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export ADMIN_TELEGRAM_ID=...
export WEBHOOK_SECRET=...
python bot.py
```
