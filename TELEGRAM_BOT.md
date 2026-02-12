# Bot de Telegram - Oráculo Agrónomo CER

## Configuración

El bot está configurado para leer su token desde `.env`:

```env
TELEGRAM_BOT_TOKEN=tu_token_aqui
```

## Instalación de dependencias

Si aún no instalaste las dependencias:

```bash
pip install -r requirements.txt
```

## Ejecutar el bot

Para iniciar el bot de Telegram, ejecuta:

```bash
python run_bot.py
```

El bot se mantendrá ejecutándose y escuchando mensajes. Para detenerlo, presiona `Ctrl+C`.

## Funcionalidades

### Menú Principal

Cuando un usuario envía `/start` o cualquier mensaje, el bot muestra dos botones:

1. **🔬 Generar Investigación** 
   - Status: En desarrollo
   - Muestra mensaje: "Función en desarrollo"

2. **📚 Consultar Base de Datos CER**
   - Muestra información sobre la herramienta
   - Permite consultar la base de datos RAG
   - Procesa preguntas agronómicas y devuelve respuestas con referencias

### Flujo de Consulta

1. Usuario presiona "Consultar Base de Datos CER"
2. Bot explica qué se puede consultar y muestra ejemplos
3. Usuario escribe su pregunta
4. Bot busca en la base de datos usando el sistema RAG
5. Bot responde con información formateada para Telegram
6. Bot ofrece opciones: "Nueva consulta" o "Menú principal"

## Formato de Respuestas

Las respuestas están optimizadas para Telegram:
- Markdown compatible
- Sin emojis decorativos de ChatGPT
- Explicaciones claras de dosis (notación agronómica estándar)
- Explicaciones detalladas de momentos de aplicación
- Links a fuentes al final

## Características Técnicas

- ✅ API de Telegram directa (no requiere ngrok ni API propia)
- ✅ Integración con pipeline RAG existente
- ✅ Mensajes largos divididos automáticamente (límite 4096 caracteres)
- ✅ Manejo de errores robusto
- ✅ Logging completo
- ✅ Estado de conversación por usuario

## Estructura de Archivos

```text
src/oraculo/telegram/
├── bot.py          # Setup de Application y registro de handlers
├── handlers.py     # Adaptadores Telegram -> pipeline RAG
├── keyboards.py    # Teclados inline
├── messages.py     # Mensajes del bot
└── utils.py        # Utilidades (ej: split de mensajes largos)

run_bot.py          # Script para ejecutar el bot
```

## Logs

El bot registra toda la actividad en logs. Para ver los logs en tiempo real mientras el bot funciona, el sistema de logging ya está configurado en `src/oraculo/observability/logging.py`.

## Solución de Problemas

### El bot no responde

1. Verifica que el token en `.env` sea correcto
2. Verifica que el bot esté en ejecución (`python run_bot.py`)
3. Revisa los logs para ver errores

### Error al buscar en la base de datos

1. Verifica que Qdrant esté accesible (URL y API key en `.env`)
2. Verifica que Gemini API key sea válida
3. Revisa los logs para detalles del error

### Mensajes muy largos

El bot automáticamente divide mensajes que excedan 4096 caracteres en múltiples mensajes.
