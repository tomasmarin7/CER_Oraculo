# CER Oraculo Publico

Asistente RAG para consultas agronomicas sobre informes CER, con integracion a Telegram.

## Requisitos

- Python 3.12+
- Qdrant Cloud (URL + API key)
- Gemini API key
- Telegram bot token

## Instalacion

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Variables de entorno

Copiar `.env.example` a `.env` y completar:

- `QDRANT_URL`
- `QDRANT_API_KEY`
- `QDRANT_COLLECTION`
- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`

## Estructura del proyecto

```text
src/oraculo/
  config.py             # Configuracion central (Pydantic Settings)
  observability/        # Logging
  providers/            # Gemini (LLM, embeddings, refinamiento de consultas)
  rag/                  # Pipeline RAG y CLIs de depuracion
  sources/              # Resolucion y formateo de fuentes
  telegram/             # Integracion de Telegram (bot, handlers, mensajes)
  vectorstore/          # Cliente Qdrant y operaciones de busqueda
run_bot.py              # Entry point para iniciar Telegram bot
tests/                  # Scripts/pruebas de validacion manual
```

## Ejecucion

Iniciar bot:

```bash
python run_bot.py
```

Probar retrieval desde CLI:

```bash
PYTHONPATH=src python -m oraculo.rag.retrieve_cli "tu pregunta"
```

Ver prompt final:

```bash
PYTHONPATH=src python -m oraculo.rag.context_cli "tu pregunta"
```

Generar respuesta por CLI:

```bash
PYTHONPATH=src python -m oraculo.rag.answer_cli "tu pregunta"
```
