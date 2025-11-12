# Tennis Watch Bot (Telegram)

Бот следит за выбранными игроками и присылает карточку с результатом СРАЗУ после окончания матча.

**Источник данных:** неофициальные JSON‑эндпоинты SofaScore. Будьте внимательны к их условиям использования. Flashscore не реализован (сложно из‑за сокетов/токенов), но интерфейс провайдера допускает добавление.

## Быстрый старт (локально)

1. Установите Python 3.11+
2. `pip install -r requirements.txt`
3. Создайте `.env` или выставьте переменные окружения:
   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC...your-token
   TZ=Europe/Helsinki
   POLL_SECONDS=75
   DATA_SOURCE=sofascore
   DB_PATH=bot.db
   ```
4. Запустите: `python -u bot.py`

## Docker
```bash
docker build -t tennis-watch-bot .
docker run --rm -e TELEGRAM_BOT_TOKEN=XXX -e TZ=Europe/Helsinki tennis-watch-bot
```

## Команды в боте
- `/start` — приветствие и справка
- `/watch Имя1, Имя2, ...` — начать следить за этими игроками сегодня (до 23:59 по вашему TZ)
- `/add Имя` — добавить игрока в список на сегодня
- `/remove Имя` — убрать игрока из списка
- `/list` — показать текущий список на сегодня
- `/clear` — очистить список на сегодня
- `/tz Europe/Helsinki` — настроить часовой пояс
- `/format` — показать пример форматирования итогового сообщения
- `/help` — справка и список ошибок

## Как работает оповещение
- Бот опрашивает SofaScore каждые `POLL_SECONDS` секунд.
- Ищет все матчи за **сегодня** для игроков из вашего списка.
- Как только матч получает статус `finished`, бот запрашивает детальную статистику и присылает карточку.
- Идентичные матчи не дублируются: хранится список уже отправленных событий (таблица `notified`).

## Формат сообщения (пример)
```
Lorenzo Musetti — Alex de Minaur
Счёт: 7:5, 3:6, 7:5
Время: 2:48

Musetti

Эйсы: 5
Двойные: 3
% попадания первой подачи: 66%
Очки выигр. на п.п.: 63%
Очки выигр. на в.п.: 74%
Виннеры: 22
Невынужденные: 28
Спасенные б.п.: 3/5
Спасенные м.б.: 0

De Minaur

Эйсы: 10
Двойные: 0
% попадания первой подачи: 66%
Очки выигр. на п.п.: 66%
Очки выигр. на в.п.: 59%
Виннеры: 34
Невынужденные: 44
Спасенные б.п.: 9/12
Спасенные м.б.: 1
```

### Важные нюансы по данным
- Не все турниры публикуют *виннеры* и *невынужденные* — тогда в карточке будет `н/д`.
- `Спасенные м.б.` (матчболы) редко доступны. Также будет `н/д`, если источника нет.
- Продолжительность матча берём, если доступна у SofaScore (`incidents.length`). Иначе `н/д`.

## Обработка ошибок (копируйте для репорта)
- `E_SOFASCORE_HTTP_<status>` — ошибка HTTP от SofaScore. Возможные причины: сетевой сбой, блокировка, изменение API.
- `E_PARSE_STATS_MISSING` — формат статистики изменился; парсер не нашёл ожидаемые поля.
- `E_NO_EVENTS_TODAY` — не найдено матчей сегодня по вашему списку.
- `E_TG_SEND` — Telegram отклонил сообщение (rate limit / блок).
- `E_DB_LOCKED` — база данных занята другой операцией.

В идеале отправляйте лог целиком со стеком (если есть).

## Развёртывание
### Render / Railway / любой VPS (long polling)
- Запускаем контейнер или `python -u bot.py` как systemd‑сервис (`sample_systemd.service`). Long polling устойчив и не требует вебхука.

### Вебхук‑режим
- В данный минимальный релиз вебхук не включён по умолчанию (long polling проще). 
  Если нужен вебхук (например, на Cloud Run), напишите issue — добавим `FastAPI` endpoint.

## Расширение: Flashscore
- Поддержка Flashscore не включена из‑за сложной авторизации, сокетов и частых изменений.
  Архитектура позволяет добавить `providers/flashscore.py` и переключаться через `DATA_SOURCE`.

## Юридическое
- Проект использует неофициальные эндпоинты SofaScore для личного/редакционного использования. 
  Соблюдайте условия использования источника. Ответственность за использование лежит на вас.



## GitHub-first (CI/CD)

В репозитории уже есть готовые сценарии:
- `.github/workflows/ci-and-docker.yml` — CI + сборка и публикация Docker-образа в GHCR (`ghcr.io/<org>/<repo>`).
- `.github/workflows/deploy-flyio.yml` — (опционально) автодеплой на Fly.io при пуше в `main` или вручную (workflow_dispatch).
- `render.yaml` — блюпринт для Render (тип `worker`, сборка из `Dockerfile`). Подключите репозиторий в Render и включите Auto Deploy.

### Secrets (GitHub → Settings → Secrets and variables → Actions)
- `FLY_API_TOKEN` — токен Fly.io (если используете deploy action).
> `TELEGRAM_BOT_TOKEN` хранится на хостинге (Render/Fly/Railway), в CI он не нужен.

### Публикация в GHCR
Автоматически на каждый push в `main` и на теги `v*`/`release-*`:
- Логин в GHCR по `GITHUB_TOKEN`.
- Сборка по `Dockerfile`.
- Пуш тэгов: для `main` — `latest` и `sha`, для тегов — `vX.Y.Z` и т.д.

### Деплой на Fly.io
1) Создайте приложение `flyctl launch` (один раз) или используйте action.
2) Добавьте секрет `FLY_API_TOKEN` в GitHub.
3) Установите секреты/переменные окружения на Fly.io (`TELEGRAM_BOT_TOKEN`, `TZ`, `POLL_SECONDS`, `DATA_SOURCE`).
4) Пуш в `main` запускает деплой GitHub Action.

### Деплой на Render (рекомендовано для background-воркера)
1) Подключите репозиторий к Render.
2) Создайте Service типа **Worker**, источник — `render.yaml`.
3) Задайте переменные окружения (секреты) в Render: `TELEGRAM_BOT_TOKEN`, `TZ`, `POLL_SECONDS`, `DATA_SOURCE`, `DB_PATH`.
4) Включите Auto Deploy — сборка из `Dockerfile` при каждом пуше в `main`.

### Railway / Heroku
Используйте `Procfile` (`worker: python -u bot.py`) + переменные окружения на платформе.

### Почему не GitHub Actions как «хостинг»
GH Actions не предназначён для постоянных заданий: есть лимиты по времени выполнения и биллингу. Используйте Fly/Render/Railway/VPS для постоянной работы бота.



## Vercel (Webhooks + Cron)

Архитектура под Vercel:
- `/api/webhook` — FastAPI-приложение, принимает Telegram обновления (команды).
- `/api/cron` — FastAPI-приложение, выполняется по **Vercel Cron** каждые 2 минуты и шлёт карточки по завершённым матчам.
- Хранилище — **Vercel Postgres** (через `POSTGRES_URL`). Схема создаётся автоматически при первом запуске.

### Шаги
1) В Vercel импортируйте репозиторий → Framework «Other».  
2) В Project → Settings → Environment Variables добавьте:
   - `TELEGRAM_BOT_TOKEN` — токен Telegram-бота
   - `WEBHOOK_SECRET` — секрет для проверки вебхука (любой сложный UUID)
   - `POSTGRES_URL` — строка подключения к Vercel Postgres
   - опционально: `TZ`, `POLL_SECONDS`, `DATA_SOURCE` (по умолчанию `sofascore`)
3) В корне есть `vercel.json` с cron: `*/2 * * * *` вызывает `/api/cron`.
4) Задеплойте проект (Vercel сам соберёт зависимости из `requirements.txt`).
5) Пропишите webhook Telegram с секретом:
   ```bash
   curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://<project>.vercel.app/api/webhook",
       "secret_token": "<WEBHOOK_SECRET>"
     }'
   ```
   Замените `<project>` и секрет.

### Проверка
- Напишите боту `/start` → получите справку.
- `/watch Sinner, Rublev` → бот добавит игроков на сегодня.
- Подождите завершения матчей или измените cron на `* * * * *` на время теста.

### Ограничения Vercel
- Постоянные бэкграунд-процессы на Vercel невозможны — поэтому используется cron каждые 2 мин.
- Всплески трафика и длительные ответы — держите обработку webhook максимально быстрой.

### Миграции БД
- Схема создаётся автоматически (`ensure_schema()`), отдельный шаг миграции не требуется.
