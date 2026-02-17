# Plan de refactor total

## Objetivos

1. Mantener comportamiento conversacional requerido:
- Primer mensaje siempre responde introduccion.
- Router global decide accion segun conversacion completa.
- Flujo CER: query enhancer -> RAG Qdrant `cer_chunks` -> build context -> listar ensayos.
- Seguimiento de detalles con `guided_detail_followup.md` + fuentes.
- Posibilidad de salto a SAG y retorno al router.
- Sesion expira a los 15 minutos.

2. Ordenar el codigo para escalado AWS:
- Logica de negocio aislada del canal.
- Persistencia por interfaz de repositorio.
- Preparado para SQS + Lambdas + DynamoDB.

## Fases

### Fase 1 (implementada en este commit)
- Crear capa `aplicacion/` con servicio conversacional reutilizable.
- Mover respuestas estandar y helpers de texto a modulos dedicados.
- Reducir duplicacion de parseo JSON y carga de prompts.
- Dejar Telegram como adaptador fino.
- Documentar arquitectura AWS objetivo.

### Fase 2 (siguiente)
- Separar acciones de router en enum de dominio (`AccionGlobal`).
- Unificar router global y router de seguimiento en un enrutador jerarquico.
- Extraer puertos para:
  - LLM
  - RAG CER
  - RAG SAG
  - Repositorio de sesiones (DynamoDB)
  - Archivador conversacional (S3/DynamoDB)

### Fase 3 (siguiente)
- Implementar adaptadores AWS reales:
  - Repositorio DynamoDB con TTL 15 min.
  - Publicador/consumidor SQS para trabajos RAG.
  - Handler webhook WhatsApp.

### Fase 4 (siguiente)
- Renombrado completo de modulos legacy a nombres finales en espanol.
- Eliminacion de compatibilidad temporal.
- Tests de integracion por flujo CER/SAG/detalle/clarificacion.

## Criterios de aceptacion del refactor

- Flujo end-to-end funciona en Telegram sin regresiones.
- Codigo de negocio ejecutable fuera de Telegram.
- No hay duplicacion critica de utilidades comunes.
- Arquitectura documentada para despliegue AWS.
