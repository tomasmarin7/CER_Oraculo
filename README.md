# CER Oraculo Publico

Asistente conversacional agronomico para Telegram con consultas de ensayos CER y apoyo con datos SAG.

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
- `QDRANT_SAG_COLLECTION`
- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- Opcionales de rendimiento/log:
  - `ORACULO_LOG_LEVEL=INFO|DEBUG`
  - `RAG_USE_QUERY_REFINER=true|false`
  - `GEMINI_THINKING_BUDGET=0` (recomendado para menor latencia)
  - Router rapido:
    - `GEMINI_ROUTER_MODEL=gemini-2.5-flash`
    - `GEMINI_ROUTER_THINKING_BUDGET=0`
  - Operacion 24/7:
    - `TELEGRAM_CONCURRENT_UPDATES=64`
    - `ORACULO_WORKER_THREADS=24`
    - `ORACULO_SESSION_CLEANUP_INTERVAL_SECONDS=60`
    - `ORACULO_MAX_SESIONES_EN_MEMORIA=1000`
  - Respuestas complejas:
    - `GEMINI_COMPLEX_MODEL=gemini-3-pro-preview`
    - `GEMINI_COMPLEX_MAX_OUTPUT_TOKENS=4096`
    - `GEMINI_COMPLEX_THINKING_BUDGET=1536`
    - `GEMINI_COMPLEX_TIMEOUT_MS=120000`

## Ejecucion

```bash
python run_bot.py
```

## Arquitectura del codigo (refactor)

```text
src/oraculo/
  main.py                               # Entry principal
  config.py                             # Configuracion central
  aplicacion/                           # Casos de uso y orquestacion conversacional
    servicio_conversacion_oraculo.py    # Flujo completo del turno (agnostico de canal)
    modelos_oraculo.py                  # DTOs de salida
    texto_oraculo.py                    # Mensajes estandar y helpers de texto
    utiles_prompt.py                    # Utilidades comunes de prompt/json
  conversation/                         # Dominio de sesiones y estado conversacional
  router/                               # Router global de acciones
  followup/                             # Router/prompts de detalle y seguimiento
  rag/                                  # Retrieval + armado de contexto documental
  vectorstore/                          # Cliente y busqueda Qdrant
  providers/                            # Integracion Gemini/embeddings/refiner
  sources/                              # Resolucion/formato de fuentes CER
  telegram/                             # Adaptador de canal Telegram (polling)

run_bot.py                 # Script de arranque
index.csv                  # Indice de fuentes CER
```

## Flujo conversacional

1. Usuario escribe cualquier mensaje.
2. En primera interaccion de la sesion se responde con el mensaje introductorio estandar.
3. Cada nuevo mensaje pasa por `router/global_router.py`.
4. Si corresponde busqueda CER/SAG o detalle, `conversation/flujo_guiado.py` ejecuta el flujo RAG.
5. Si corresponde chat corto o aclaracion, se responde sin retrieval.
6. Si pasan 15 minutos sin mensajes, la sesion expira y la proxima interaccion parte desde cero.

## Arquitectura AWS objetivo

Documento de referencia: `docs/arquitectura_aws_objetivo.md`

Resumen:
- `canal-telegram-poller` (Lambda o contenedor) ingresa mensajes.
- `router-conversacion` (Lambda) ejecuta `ServicioConversacionOraculo`.
- `worker-rag-cer` y `worker-rag-sag` (Lambda) procesan consultas pesadas por SQS.
- `dynamodb-sesiones` persiste estado de sesion y expiracion por TTL.
- `dynamodb-conversaciones` (o S3) guarda historico.
- `api-whatsapp-webhook` se conecta al mismo servicio conversacional.

## Notas

- El servicio conversacional ya esta desacoplado del canal y puede ejecutarse desde Telegram, WhatsApp webhook o Lambdas.
- El repositorio actual usa memoria para sesiones (`AlmacenSesionesMemoria`) y archivos JSON para archivo historico.
- Hay limpieza periodica en background: cada `ORACULO_SESSION_CLEANUP_INTERVAL_SECONDS` se remueven de RAM sesiones inactivas por 15 minutos y se conservan archivadas en `data/conversations/`.
- Para respuestas mas rapidas, usa `GEMINI_MODEL=gemini-2.5-flash` y `GEMINI_THINKING_BUDGET=0`.

## Produccion 24/7 (Linux con systemd)

1. Crear y completar `.env`.
2. Ajustar `WorkingDirectory`, `User` y rutas en `deploy/systemd/oraculo-telegram.service`.
3. Ejecutar una sola instancia del poller de Telegram por token (si levantas 2 procesos en polling, duplicarás respuestas).
4. Instalar servicio:

```bash
sudo cp deploy/systemd/oraculo-telegram.service /etc/systemd/system/oraculo-telegram.service
sudo systemctl daemon-reload
sudo systemctl enable --now oraculo-telegram.service
```

5. Ver estado y logs:

```bash
sudo systemctl status oraculo-telegram.service
journalctl -u oraculo-telegram.service -f
```
