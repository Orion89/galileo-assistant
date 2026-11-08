# Walkthrough: Migración de Galileo AI a OpenAI Agents SDK

Se ha completado con éxito la migración de **Galileo AI** desde la **OpenAI Assistants API** hacia el paquete oficial **OpenAI Agents SDK (`openai-agents`)** integrado con la interfaz conversacional de **Chainlit**.

---

## 🚀 Cambios Realizados

### 1. Dependencias y Entorno
- **[requirements.txt](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/requirements.txt)**:
  - Actualizado `chainlit>=2.6.0` (resolviendo en 2.12.0) compatible con `openai-agents>=0.24.0`.
  - Incorporado `openai>=3.0.0` (3.46.0).
  - Añadidos `aiohttp>=3.10.0` y `pydantic>=2.10.0`.
- **[.gitignore](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/.gitignore)**:
  - Añadidas exclusiones para entornos virtuales (`.venv/`, `venv/`), bases de datos SQLite (`*.db`, `*.db-shm`, `*.db-wal`) y logs de aplicación.
- Entorno virtual local `.venv` creado con Python 3.12.8 e instalación completa de dependencias.

---

### 2. Capa de Configuración del Agente
- **[assistants/galileo_agent.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/assistants/galileo_agent.py)** *(NUEVO)*:
  - Función fábrica `create_galileo_agent(vector_store_id=..., model_override=...)`.
  - Parametrización mediante la variable de entorno `OPENAI_MODEL` (con fallback dinámico a `config_galileo_v1.1.0.json` o `"gpt-4.1"`).
  - Carga automática de las instrucciones del sistema desde `config_galileo_v1.1.0.json`.
  - Configuración de hiperparámetros con `ModelSettings(temperature=0.25, top_p=0.95)`.
  - Registro de las 4 herramientas del agente:
    1. `FileSearchTool(vector_store_ids=[...])`
    2. `CodeInterpreterTool(tool_config={"type": "code_interpreter"})`
    3. `download_external_file_tool`
    4. `rww_evaluate_tool`

---

### 3. Capa de Herramientas (Tools)
- **[tools/functions/rww_evaluate.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/tools/functions/rww_evaluate.py)**:
  - Decorada como `@function_tool` oficial del SDK (`rww_evaluate_tool(payload: RWWInput)`).
  - Mantiene las funciones de cálculo matemático, scoring ponderado 7D, análisis financiero (EBITDA, Payback, TCO), Killer Questions y generación de planes piloto.
  - Soporta invocación directa (programática) y vía llamada a herramienta del agente.
- **[tools/functions/download_external_file.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/tools/functions/download_external_file.py)**:
  - Decorada como `@function_tool` asíncrona (`download_external_file_tool(url: str)`).
  - Descarga archivos con `aiohttp`, valida extensiones soportadas y los indexa automáticamente en el Vector Store efímero de la sesión.
- **[tools/definitions/rww.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/tools/definitions/rww.py)** y **[tools/definitions/download_files.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/tools/definitions/download_files.py)**:
  - Actualizados para reexportar tanto los esquemas tradicionales como las nuevas instancias de `@function_tool`.

---

### 4. Controlador de Streaming e Interfaz de Usuario
- **[main.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/main.py)**:
  - **Eliminación del código heredado de Assistants API**: Eliminadas las clases de polling manual, `StreamHandler` personalizado, fallbacks de `run_id` y `submit_tool_outputs`.
  - **Gestión de Sesiones**: Integrado `SQLiteSession(session_id=thread_id, db_path="conversations/galileo_sessions.db")` para almacenar el historial de conversación por hilo.
  - **Vector Stores Efímeros por Sesión**: Creación dinámica con expiración automática de 1 día (`expires_after={"anchor": "last_active_at", "days": 1}`), evitando mezclar documentos entre usuarios y reduciendo costes de almacenamiento.
  - **Streaming Nativo**: Uso de `Runner.run_streamed()` escuchando `stream_events()`:
    - `raw_response_event` (`response.output_text.delta`): transmisión fluida de tokens en tiempo real a `cl.Message`.
    - `run_item_stream_event` (`tool_called` / `tool_output`): creación y actualización reactiva de pasos `cl.Step`. Formateo enriquecido para `rww_evaluate` mostrando el resumen ejecutivo y decisión (`GO`, `PILOT`, `HOLD`, `KILL`).
  - **Cancelación Inmediata**: En `@cl.on_stop` se invoca `runner_result.cancel(mode="immediate")`.
  - **Conservación de UX**: Preservados los 3 Starters, autenticación por email, integración con Chainlit Data Layer (PostgreSQL) y bienvenida en español.

---

## 🧪 Pruebas y Validación Offline

Siguiendo las instrucciones del usuario, **no se realizaron llamadas a la API de OpenAI** (para preservar API keys y cuotas). Se construyó una suite de verificación offline exhaustiva:

### Archivo de Prueba: `tests/test_offline_suite.py`

Comando ejecutado:
```powershell
.\.venv\Scripts\python.exe tests/test_offline_suite.py
```

### Resultados de la Verificación:
1. **Instanciación del Agente Galileo**:
   - `Agent.name == "Galileo"`
   - `Agent.model` configurado dinámicamente (`gpt-4.1` por defecto, sobreescribible con `OPENAI_MODEL`).
   - Las 4 herramientas registradas correctamente: `['file_search', 'code_interpreter', 'download_external_file', 'rww_evaluate']`.
2. **Scoring y Finanzas R-W-W**:
   - Evaluación con inputs de prueba completada con éxito: `Score = 94.5/100 -> Decisión = GO`.
   - EBITDA incremental anual: `$30,000 USD`.
   - Inversión total: `$60,000 USD`.
   - Payback: `2.0 años`.
   - Serialización de esquema `RWWEvaluation` validada.
3. **Mecanismo de Killer Questions**:
   - Detección de respuesta negativa en pregunta crítica con auto-kill verificado: `Decisión = KILL`.
4. **Persistencia SQLiteSession**:
   - Inicialización y aislamiento de sesión verificado con `SQLiteSession`.
5. **Carga Limpia de `main.py`**:
   - `main.py` importa sin advertencias ni errores de sintaxis, y el callback de autenticación por email valida correctamente.
