# Second Brain

Локальное приложение «второй мозг»: всё, что вы пишете, запоминается в SQLite и
доступно для семантического поиска. Ответы генерирует локальная модель
**Gemma 4 12B** (`google/gemma-4-12B-it`) с квантованием 4-бит, через HuggingFace
Transformers. Интерфейс — локальное веб-приложение (FastAPI + браузер).

## Возможности

- **Автозапоминание:** каждое сообщение в чате сохраняется как «воспоминание».
- **RAG-recall:** к вашему запросу подтягиваются top-k похожих воспоминаний
  (эмбеддинги `all-MiniLM-L6-v2` + FAISS) и вставляются в системный промпт.
- **Стриминг ответов** модели по Server-Sent Events.
- **Слэш-команды** для управления памятью и режимами.
- Весь индекс FAISS — производный кеш; SQLite — единый источник правды (индекс
  пересобирается из базы).

## Слэш-команды

| Команда | Действие |
| --- | --- |
| `/help` | список команд |
| `/save <текст>` | явно сохранить воспоминание |
| `/search <запрос>` | семантический поиск по памяти |
| `/recent [n]` | последние n записей |
| `/tags` | список тегов с количеством |
| `/tag <id> <тег>` | добавить тег к записи |
| `/forget <id>` | удалить одно воспоминание |
| `/summary [n]` | резюме последних записей (через LLM) |
| `/context [запрос]` | показать, что подставляется в промпт |
| `/export` | экспорт всей памяти в JSON |
| `/clear` | очистить диалог (память остаётся) |
| `/think on\|off` | режим размышлений (thinking mode) |
| `/wipe everything` | удалить ВСЮ память (деструктивно) |
| `/status` | состояние приложения |

## Установка и запуск

> Целевое железо: NVIDIA RTX 5070 Ti (Blackwell, sm_120) + 32 ГБ ОЗУ.

1. **PyTorch с CUDA 12.8+** (обязательно для Blackwell):
   ```bash
   pip install torch --index-url https://download.pytorch.org/whl/cu128
   ```
2. **Зависимости проекта:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Доступ к модели (gated).** Gemma требует принятия лицензии на HuggingFace:
   откройте `https://huggingface.co/google/gemma-4-12B-it`, согласитесь с условиями,
   затем авторизуйтесь локально:
   ```bash
   huggingface-cli login
   ```
4. **Запуск:**
   ```bash
   python run.py
   ```
   Браузер откроется на `http://127.0.0.1:8000`. Опции: `--no-browser`, `--port`,
   `--reload`.

Первый запуск скачает веса (~24 ГБ, квантуются в 4-бит при загрузке) и поднимет
индекс FAISS. Модель LLM грузится лениво при первом запросе.

## Структура проекта

```
app/
  config.py            настройки (модель, пути, параметры генерации)
  main.py              фабрика FastAPI, сборка сервисов, раздача веба
  api/routes.py        /api/chat (SSE), /api/command, /api/memories, /api/health
  llm/gemma.py         загрузка Gemma 4 (4-bit), стриминг, очистка thinking-блока
  memory/store.py      SQLite CRUD (воспоминания, теги)
  memory/embeddings.py Embedder (MiniLM) + FaissIndex
  memory/recall.py     RAG: поиск + сборка контекста
  chat/session.py      история диалога
  chat/commands.py     реестр и обработчики слэш-команд
web/                   интерфейс (index.html, style.css, app.js)
data/                  runtime: brain.db, faiss.index (+ .ids.npy)
tests/                 pytest на моках (без GPU и тяжёлых зависимостей)
run.py                 точка входа (uvicorn)
```

## Тесты

Логика покрыта тестами с фейковым эмбеддером/индексом/LLM, поэтому запускаются
без torch/faiss/sentence-transformers:

```bash
python -m pytest -q
```

## Известные риски

- **sm_120 (Blackwell):** нужен свежий PyTorch (cu128). Старые сборки упадут в CPU.
- **bitsandbytes на Windows:** ставьте `>=0.45`; если 4-бит не заведётся — fallback
  на 8-бит (`load_in_4bit = False` в `app/config.py` не отключит квантование;
  переключайте `bnb_4bit_*`/модель под GGUF вручную).
- Модель грузится через `AutoModelForMultimodalLM`/`AutoProcessor` — требуется
  свежий `transformers` (см. `requirements.txt`).
