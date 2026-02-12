# Módulo Telegram

Capa de presentación simple para Telegram. **No duplica lógica**, solo adapta.

## 📁 Estructura

```
telegram/
├── __init__.py           # Exporta TelegramBot
├── bot.py                # Setup del bot y registro de handlers (110 líneas)
├── handlers.py           # Handlers ligeros - adapters Telegram ↔ RAG (180 líneas)
├── keyboards.py          # Teclados inline (40 líneas)
├── messages.py           # Templates de mensajes (80 líneas)
└── utils.py              # Utilidades (30 líneas)
```

## 🎯 Principio: Separación de Capas

### ✅ Esta capa (Telegram)
- **Solo presentación**: adaptar Telegram ↔ Lógica de negocio
- Handlers ligeros (< 30 líneas cada uno)
- No duplica lógica existente

### ✅ Lógica de negocio (ya existe)
- `rag/pipeline.py` → `answer()` (ya probado y funciona)
- `providers/` → LLM, embeddings, etc
- `vectorstore/` → Qdrant
- `sources/` → Resolver fuentes

## 📋 Archivos

### `bot.py`
**Responsabilidad**: Setup de Telegram y registro de handlers.

```python
class TelegramBot:
    def setup() -> Application:
        # Registra handlers
        pass
    
    def run():
        # Mantiene servicio activo (polling)
        pass
```

### `handlers.py`
**Responsabilidad**: Adapters ligeros entre Telegram y lógica de negocio.

Cada handler:
1. Recibe input de Telegram
2. Llama a la lógica existente (`rag.pipeline.answer()`)
3. Formatea respuesta para Telegram

**Sin lógica compleja**, solo adaptación.

Funciones:
- `start_command()` - /start
- `menu_callback()` - Volver al menú
- `research_callback()` - Investigación (placeholder)
- `database_callback()` - Iniciar consulta
- `handle_user_query()` - **Clave**: llama a `rag_answer()` que ya existe

### `keyboards.py`
**Responsabilidad**: Definir botones inline.

- `get_main_menu_keyboard()` - Menú principal
- `get_post_query_keyboard()` - Después de consulta

### `messages.py`
**Responsabilidad**: Templates de texto (sin hardcoding).

- `get_welcome_message()`
- `get_database_intro_message()`
- etc.

### `utils.py`
**Responsabilidad**: Utilidades de Telegram.

- `split_message()` - Dividir mensajes largos (límite 4096)

## 🔄 Flujo de una Consulta

```
Usuario escribe: "¿Cómo funciona Kelpak para uvas?"
           ↓
   handlers.handle_user_query()
           ↓
   rag.pipeline.answer()  ← YA EXISTE, YA FUNCIONA
           ↓
   Formatea para Telegram
           ↓
   Envía respuesta
```

**No se duplica lógica**. Solo se adapta.

## ✅ Ventajas de esta Arquitectura

### 1. **Reutiliza lo que funciona**
- `rag/pipeline.py` ya está probado
- No reinventamos la rueda

### 2. **Separation of Concerns**
```
telegram/      → Presentación (Telegram)
rag/           → Lógica RAG
providers/     → Servicios externos (Gemini, Qdrant)
```

### 3. **Fácil de testear**
```python
# Test del pipeline (ya existe)
result = rag.pipeline.answer("pregunta")

# Test del adapter de Telegram
result = await handlers.handle_user_query(mock_update, mock_context)
```

### 4. **Escalable**
Para agregar nuevo canal (ej: API REST):
```
src/oraculo/api/
├── server.py
└── endpoints.py  # También llaman a rag.pipeline.answer()
```

## 📊 Antes vs Después

### ❌ Antes
```
telegram/handlers/
├── menu.py       (50 líneas)
├── database.py   (110 líneas con lógica RAG duplicada)
└── research.py   (20 líneas)
```
- Lógica mezclada
- Carpeta innecesaria

### ✅ Después
```
telegram/
├── bot.py        (110 líneas - solo setup)
└── handlers.py   (180 líneas - solo adapters)
```
- Handlers ligeros
- Llaman a `rag.pipeline.answer()` existente
- Sin duplicación

## 🚀 Ejecutar el Bot

```bash
python run_bot.py
```

Mantiene el servicio activo. `Ctrl+C` para detener.
