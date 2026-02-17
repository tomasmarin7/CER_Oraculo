# Arquitectura AWS objetivo (proxima etapa)

## Objetivo

Separar el procesamiento conversacional por responsabilidades para escalar por demanda y por costo:

- Ingreso de mensajes por canal (Telegram hoy, WhatsApp despues).
- Orquestacion de flujo (router global + manejo de estado).
- Ejecucion de consultas RAG CER/SAG.
- Persistencia de sesiones y auditoria conversacional.

## Componentes

1. `lambda-canal-telegram-poller`
- Lee updates de Telegram (polling o webhook).
- Convierte el update a evento interno.
- Publica en `sqs-entrada-conversacion`.

2. `lambda-canal-whatsapp-webhook`
- Recibe webhook de WhatsApp.
- Normaliza payload al mismo contrato del evento interno.
- Publica en `sqs-entrada-conversacion`.

3. `lambda-router-conversacion`
- Consume `sqs-entrada-conversacion`.
- Carga/actualiza sesion en DynamoDB.
- Ejecuta `ServicioConversacionOraculo`.
- Cuando necesita retrieval pesado, deriva a cola especifica (`sqs-rag-cer`/`sqs-rag-sag`).
- Responde al canal correspondiente.

4. `lambda-worker-rag-cer`
- Ejecuta query enhancer, embeddings, consulta Qdrant `cer_chunks`, build context y generacion de respuesta.

5. `lambda-worker-rag-sag`
- Ejecuta retrieval sobre coleccion SAG y construye respuesta regulatoria/comercial.

6. `dynamodb-sesiones`
- PK: `user_id`
- SK opcional: `session_id`
- TTL de 15 minutos para expiracion automatica.

7. `dynamodb-historico` o `s3://conversations`
- Guarda sesiones cerradas para auditoria y analitica.

8. `secrets-manager`
- API keys de Gemini, Qdrant y tokens de bots.

## Contrato de evento sugerido

```json
{
  "canal": "telegram",
  "user_id": "12345",
  "mensaje": "texto del usuario",
  "timestamp": 1771253055,
  "metadata": {
    "chat_id": "12345",
    "message_id": "6789"
  }
}
```

## Principios de implementacion

- Un solo servicio de negocio (`ServicioConversacionOraculo`) reutilizado por todos los canales.
- Adaptadores finos por canal (Telegram/WhatsApp) sin logica de negocio.
- Orquestacion explicita de acciones del router global.
- Persistencia desacoplada mediante interfaz de repositorio de sesiones.
- Idempotencia por `message_id` en consumidores SQS.
