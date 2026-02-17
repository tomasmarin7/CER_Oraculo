# Bot de Telegram - Oraculo Agronomo CER

## Configuracion

En `.env`:

```env
TELEGRAM_BOT_TOKEN=tu_token
```

Instalar dependencias:

```bash
pip install -r requirements.txt
```

Ejecutar:

```bash
python run_bot.py
```

## Flujo actual (alto nivel)

1. Llega mensaje del usuario por Telegram.
2. `telegram/handlers.py` delega en `aplicacion/servicio_conversacion_oraculo.py`.
3. `router/global_router.py` decide accion global.
4. Si aplica, `conversation/flujo_guiado.py` ejecuta flujo CER/SAG.
5. En follow-up, `followup/router.py` decide accion de seguimiento.
6. Se genera respuesta con prompts y contexto RAG.
7. Si es detalle de informe, se agregan fuentes CER al final.

## Estructura relevante

```text
src/oraculo/telegram/
  bot.py
  handlers.py
  keyboards.py
  messages.py
  utils.py

src/oraculo/aplicacion/
  servicio_conversacion_oraculo.py
  modelos_oraculo.py
  texto_oraculo.py
  utiles_prompt.py

src/oraculo/router/
  global_router.py
  prompts/global_router.md

src/oraculo/followup/
  router.py
  prompting.py
  prompts/guided_followup_router.md
  prompts/guided_detail_followup.md
  prompts/guided_chat_followup.md

src/oraculo/conversation/prompts/
  listar_ensayos.md

src/oraculo/providers/prompts/
  refine_question.md
```

## Observabilidad

- Logging central: `src/oraculo/observability/logging.py`
- Conversaciones archivadas: `data/conversations/`
