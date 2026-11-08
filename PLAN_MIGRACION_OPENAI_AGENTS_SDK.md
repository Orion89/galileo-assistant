# Plan de Migración de Galileo AI: de OpenAI Assistants API a OpenAI Agents SDK

## Descripción del Objetivo

El proyecto actual **Galileo AI** es un asistente conversacional para Corporate Venture Capital (CVC) e innovación corporativa. Actualmente está construido sobre la **OpenAI Assistants API** y servido mediante la interfaz de usuario de **Chainlit**.

El objetivo de esta migración es rediseñar y reconstruir el agente y sus herramientas utilizando el nuevo paquete oficial **OpenAI Agents SDK (`openai-agents`)**, preservando todas sus capacidades actuales (revisión de decks, evaluación R-W-W, descarga de archivos externos, búsqueda vectorial y ejecución de código), mejorando la confiabilidad del streaming en Chainlit y eliminando la complejidad del manejo manual de `runs` e intercepciones de herramientas.

---

## Decisiones de Diseño Confirmadas por el Usuario

1. **Modelo de Lenguaje Parametrizado**:  
   Se utiliza la variable de entorno `OPENAI_MODEL` (con fallback a `"gpt-4.1"` o `"gpt-4o"`).
2. **Persistencia de Sesiones**:  
   Se utiliza `SQLiteSession` del SDK vinculado al ID de hilo de Chainlit (`cl.context.session.thread_id`), almacenado en `conversations/galileo_sessions.db`, garantizando soporte multi-turno aislado tanto en local como en Railway.
3. **Vector Store Efímero por Sesión**:  
   Para cada sesión donde se requiera análisis documental o subida de decks, se crea/asocia un Vector Store efímero vinculado al hilo (`thread_id`), evitando contaminar el Vector Store global y aislando la información sensible de cada usuario.
4. **Restricción de Pruebas**:  
   No se ejecutarán llamadas reales a la API de OpenAI durante la verificación (para no requerir validación de API keys ni consumir cuotas). Toda verificación se hará mediante tests de sintaxis, compilación, unit tests locales de herramientas y pruebas de estructura.

---

## Arquitectura de la Solución

```mermaid
flowchart TD
    User([Usuario en Navegador]) <-->|WebSocket / UI| ChainlitUI[Chainlit UI - main.py]
    
    subgraph "Controlador y Streaming (main.py)"
        CL_Events[Eventos Chainlit: @cl.on_message / @cl.on_chat_start]
        StreamHandler[Runner.run_streamed + stream_events]
        SessionStore[(SQLiteSession: conversations/galileo_sessions.db)]
        EphemeralVS[Vector Store Efímero por Sesión]
    end
    
    subgraph "OpenAI Agents SDK (Galileo Agent)"
        GalileoAgent[Agent: Galileo]
        PromptConfig[Instrucciones del Sistema: Galileo v1.1.0]
    end
    
    subgraph "Capa de Herramientas (Tools)"
        ToolRWW[@function_tool: rww_evaluate]
        ToolDownload[@function_tool: download_external_file]
        ToolFS[FileSearchTool - Vector Store Efímero]
        ToolCI[CodeInterpreterTool]
    end
    
    CL_Events --> SessionStore
    CL_Events --> EphemeralVS
    CL_Events --> StreamHandler
    StreamHandler --> GalileoAgent
    GalileoAgent --> PromptConfig
    GalileoAgent --> ToolRWW
    GalileoAgent --> ToolDownload
    GalileoAgent --> ToolFS
    GalileoAgent --> ToolCI
    
    ToolRWW --> PydanticRWW[Pydantic Models: RWWInput / RWWEvaluation]
    StreamHandler -->|Streaming de Tokens & Steps| ChainlitUI
```

---

## Fases de Ejecución Paso a Paso

- [ ] **Fase 1: Entorno y Dependencias**: Actualizar [requirements.txt](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/requirements.txt) incorporando `openai-agents>=0.24.0` y `openai>=3.0.0`.
- [ ] **Fase 2: Módulo del Agente**: Crear [assistants/galileo_agent.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/assistants/galileo_agent.py) con soporte para `OPENAI_MODEL`, Vector Stores dinámicos/efímeros y herramientas del SDK.
- [ ] **Fase 3: Capa de Herramientas**:
  - Refactorizar [tools/functions/rww_evaluate.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/tools/functions/rww_evaluate.py) con `@function_tool` y entrada `RWWInput`.
  - Refactorizar [tools/functions/download_external_file.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/tools/functions/download_external_file.py) con `@function_tool`.
  - Crear test unitario local offline para validar la lógica y scoring de R-W-W.
- [ ] **Fase 4: Controlador Principal (Chainlit)**:
  - Reescribir [main.py](file:///C:/Users/leomo/coder/ejercicios/dl/llm/agents/galileo-assistant/main.py) con `Runner.run_streamed`, `SQLiteSession`, creación de Vector Store efímero, streaming de tokens y representación de `cl.Step`.
- [ ] **Fase 5: Verificación Offline**:
  - Compilación de todos los archivos con `py_compile`.
  - Ejecución de pruebas unitarias locales offline.
  - Validación del ciclo de inicialización y estructura.
