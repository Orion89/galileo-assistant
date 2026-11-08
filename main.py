import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional

import chainlit as cl
from chainlit import User
from chainlit.config import config
from chainlit.data import get_data_layer
from chainlit.element import Element
from dotenv import load_dotenv

load_dotenv()

from agents import Runner, SQLiteSession
from openai import AsyncOpenAI

from assistants.galileo_agent import create_galileo_agent
from tools.functions.download_external_file import CI_ONLY_EXTS, FS_EXTS

# ─────────────────────────────  LOGGING & ERRORES  ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("app.log", encoding="utf-8"), logging.StreamHandler()],
)

def _handle_exception(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        return sys.__excepthook__(exc_type, exc, tb)
    logging.critical("❌ Excepción no controlada", exc_info=(exc_type, exc, tb))

sys.excepthook = _handle_exception

# ─────────────────────────────  ENV & CONSTANTS  ───────────────────────────────
EMAIL_RX = r"^[\w\.-]+@[\w\.-]+\.\w{2,4}$"
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
DB_DIR = Path("conversations")
DB_DIR.mkdir(exist_ok=True, parents=True)
SESSIONS_DB_PATH = str(DB_DIR / "galileo_sessions.db")

config.ui.name = "Galileo"

# Cliente asíncrono para gestión de archivos y Vector Stores efímeros
oai = AsyncOpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None


# ─────────────────────────────  UTILIDADES DE ARCHIVOS  ─────────────────────────
async def _get_or_create_session_vector_store(thread_id: str) -> Optional[str]:
    """
    Obtiene o crea un Vector Store efímero de OpenAI para la sesión actual.
    Expira automáticamente tras 1 día de inactividad para optimizar costes.
    """
    if not oai:
        return None

    vs_id = cl.user_session.get("vector_store_id")
    if vs_id:
        return vs_id

    # Si existe en metadata persistente de Chainlit Data Layer, recuperarlo
    if dl := get_data_layer():
        try:
            thread_data = await dl.get_thread(thread_id)
            if thread_data and thread_data.get("metadata", {}).get("vector_store_id"):
                vs_id = thread_data["metadata"]["vector_store_id"]
                cl.user_session.set("vector_store_id", vs_id)
                return vs_id
        except Exception as e:
            logging.warning(f"No se pudo consultar metadata del hilo en Data Layer: {e}")

    # Crear nuevo Vector Store efímero
    try:
        vs = await oai.beta.vector_stores.create(
            name=f"galileo_session_{thread_id}",
            expires_after={"anchor": "last_active_at", "days": 1},
        )
        vs_id = vs.id
        cl.user_session.set("vector_store_id", vs_id)
        logging.info(f"Vector Store efímero creado para sesión {thread_id}: {vs_id}")

        if dl := get_data_layer():
            await dl.update_thread(
                thread_id=thread_id,
                metadata={"vector_store_id": vs_id},
            )
        return vs_id
    except Exception as e:
        logging.error(f"Fallo al crear Vector Store efímero: {e}")
        return None


async def _process_user_files(elements: List[Element], thread_id: str) -> list[str]:
    """
    Sube los archivos adjuntos del usuario a OpenAI File Storage y los indexa
    en el Vector Store efímero de la sesión (si son documentos) o los prepara para Code Interpreter.
    Retorna la lista de file_ids procesados.
    """
    if not elements or not oai:
        return []

    processed_ids = []
    rejected = []
    vs_id = await _get_or_create_session_vector_store(thread_id)

    for el in elements:
        ext = Path(el.path).suffix.lower()

        if ext not in FS_EXTS and ext not in CI_ONLY_EXTS:
            rejected.append(el.name)
            continue

        try:
            purpose = "assistants"
            up = await oai.files.create(file=Path(el.path), purpose=purpose)
            processed_ids.append(up.id)

            # Si es compatible con File Search y tenemos Vector Store, indexarlo
            if ext in FS_EXTS and vs_id:
                await oai.beta.vector_stores.files.create(
                    vector_store_id=vs_id,
                    file_id=up.id,
                )
                logging.info(f"Archivo {el.name} ({up.id}) indexado en Vector Store {vs_id}")
        except Exception as e:
            logging.error(f"Error procesando archivo {el.name}: {e}")
            await cl.ErrorMessage(content=f"Error al procesar el archivo {el.name}: {str(e)}").send()

    if rejected:
        await cl.Message(
            "⚠️ Formatos aún no soportados y omitidos: " + ", ".join(rejected)
        ).send()

    return processed_ids


# ─────────────────────────────  STARTERS & AUTH  ───────────────────────────────
@cl.set_starters
async def set_starters():
    return [
        cl.Starter(
            label="Formaliza tu Propuesta de Ahorro Energético",
            message="Tengo una idea de cambiar todas las ampolletas incadecentes por lámparas LED para ahorrar energía ¿Puedes formular la idea para presentársela a la gerencia de innovación de forma clara en un párrafo de 5 líneas? También, indica el monto de la inversión y el monto de dinero ahorrado. Hazme todas las preguntas necesarias para completar esto.",
            icon="/public/idea-svgrepo-com.svg",
        ),
        cl.Starter(
            label="Mejora tu idea de innovación con respaldos",
            message="Tenemos un desafío en el área de ventas. Nuestros productos en general tienen nombres que son muy poco entendibles para nuestros clientes. Esto pasa en varios de nuestros productos. Por lo tanto, sería una muy buena idea tener una forma universal para nosotros, como compañía, que nos permita entendernos de forma interna y también hacia nuestros clientes respecto a los nombres de nuestros productos.",
            icon="/public/idea-svgrepo-com.svg",
        ),
        cl.Starter(
            label="Formula tu propuesta",
            message="Tengo una idea de cambiar la leche actual que se usa en nuestra oficina por leche vegetal. ¿Me puedes ayudar a formular la idea para presentarla de forma clara al equipo de innovación en un párrafo de 5 líneas? También, indica el monto de la inversión y el monto de dinero ahorrado, indicando de dónde sacas esa información.",
            icon="/public/idea-svgrepo-com.svg",
        ),
    ]


@cl.password_auth_callback
def auth(email: str, pwd: str | None = None):
    return User(identifier=email, metadata={"email": email}) if re.match(EMAIL_RX, email) else None


# ─────────────────────────────  CICLO DE VIDA  ────────────────────────────────
@cl.on_chat_start
async def on_start():
    user = cl.user_session.get("user")
    meta = dict(user.metadata or {}) if user else {}

    email = meta.get("email") or (user.identifier if user else "Usuario")
    display_name = meta.get("name") or email

    if user and "email" not in meta:
        meta["email"] = email
        user.metadata = meta
        if dl := get_data_layer():
            if hasattr(dl, "update_user_metadata"):
                await dl.update_user_metadata(user.identifier, meta)

    welcome = (
        f"👋 **¡Hola {display_name}!**\n\n"
        "Soy **Galileo**, tu agente de IA especializado en innovación y Corporate Venture Capital.\n\n"
        "Como primer punto, es clave que sepas que un proyecto de innovación se evalúa principalmente por el **impacto** que genera para la empresa. "
        "Mi misión es ayudarte a transformar tu idea, por simple que parezca, en una propuesta de valor sólida y convincente.\n\n"
        "### Juntos podemos:\n"
        "• **Estructurar tu idea inicial** en una propuesta clara y profesional.\n"
        "• **Cuantificar el impacto potencial**, calculando ahorros, beneficios y la inversión necesaria.\n"
        "• **Construir un caso de negocio** listo para ser presentado al equipo de innovación.\n\n"
        "**¿Cómo empezamos?** Simplemente cuéntame tu idea en un par de frases.\n\n"
        "💡 _Ejemplos_: «Tengo una idea para cambiar la leche de la oficina por una opción vegetal» · «Tengo una idea de cambiar todas las ampolletas incandescentes por lámparas LED. Formula la idea para presentársela a la gerencia de innovación.»\n\n"
        "--- \n"
        "**P.D. ¿Ya tienes un proyecto avanzado o un deck de inversión?**\n"
        "También puedo ayudarte a analizarlo en profundidad, evaluar riesgos y compararlo con benchmarks de la industria. Simplemente adjunta el archivo para comenzar."
    )
    await cl.Message(welcome).send()

    thread_id = cl.context.session.thread_id
    cl.user_session.set("thread_id", thread_id)

    if dl := get_data_layer():
        if user and await dl.get_user(user.identifier) is None:
            await dl.create_user(user)
        await dl.update_thread(
            thread_id=thread_id,
            metadata={"session_id": thread_id},
        )


@cl.on_chat_resume
async def on_resume(meta):
    thread_id = meta.get("id") or cl.context.session.thread_id
    cl.user_session.set("thread_id", thread_id)
    if vs_id := meta.get("metadata", {}).get("vector_store_id"):
        cl.user_session.set("vector_store_id", vs_id)


# ─────────────────────────────  MANEJO DE MENSAJES  ───────────────────────────
@cl.on_message
async def on_message(msg: cl.Message):
    thread_id = cl.user_session.get("thread_id") or cl.context.session.thread_id

    # 1. Procesar archivos adjuntos si existen
    if msg.elements:
        await _process_user_files(msg.elements, thread_id)

    # 2. Configurar agente y sesión multi-turno persistente
    vs_id = cl.user_session.get("vector_store_id")
    agent = create_galileo_agent(vector_store_id=vs_id)
    session = SQLiteSession(session_id=thread_id, db_path=SESSIONS_DB_PATH)

    # 3. Mostrar spinner de pensamiento
    spin_msg = await cl.Message("🤖 Pensando").send()

    # 4. Iniciar ejecución en streaming con OpenAI Agents SDK
    runner_result = Runner.run_streamed(
        agent,
        input=msg.content,
        session=session,
    )
    cl.user_session.set("runner_result", runner_result)

    response_msg: Optional[cl.Message] = None
    active_steps: Dict[str, cl.Step] = {}

    try:
        async for event in runner_result.stream_events():
            # A) Streaming de texto incremental (tokens del modelo)
            if event.type == "raw_response_event":
                data = getattr(event, "data", None)
                if data and getattr(data, "type", None) == "response.output_text.delta":
                    delta_text = getattr(data, "delta", "")
                    if delta_text:
                        if spin_msg:
                            await spin_msg.remove()
                            spin_msg = None
                        if response_msg is None:
                            response_msg = await cl.Message(author="Galileo", content="").send()
                        await response_msg.stream_token(delta_text)

            # B) Eventos de elementos de ejecución (tools, handoffs, etc.)
            elif event.type == "run_item_stream_event":
                event_name = getattr(event, "name", None)
                item = getattr(event, "item", None)

                # Inicio de llamada a herramienta
                if event_name == "tool_called" or getattr(item, "type", None) == "tool_call_item":
                    if spin_msg:
                        await spin_msg.remove()
                        spin_msg = None

                    tool_name = getattr(item, "tool_name", None) or "herramienta"
                    call_id = getattr(item, "call_id", None) or tool_name

                    step = cl.Step(name=tool_name, type="tool")
                    if tool_name == "code_interpreter":
                        step.show_input = "python"
                    step.start = datetime.now(timezone.utc).isoformat()
                    await step.send()
                    active_steps[call_id] = step

                # Retorno de salida de herramienta
                elif event_name == "tool_output" or getattr(item, "type", None) == "tool_call_output_item":
                    call_id = getattr(item, "call_id", None)
                    output_data = getattr(item, "output", "")

                    # Buscar el step correspondiente
                    step = active_steps.get(call_id)
                    if not step and active_steps:
                        # Fallback al último step activo
                        call_id, step = active_steps.popitem()
                        active_steps[call_id] = step

                    if step:
                        step.end = datetime.now(timezone.utc).isoformat()
                        formatted_output = str(output_data)

                        # Formato visual enriquecido para rww_evaluate
                        try:
                            parsed = json.loads(output_data) if isinstance(output_data, str) else output_data
                            if isinstance(parsed, dict) and "total_score" in parsed:
                                short_summary = parsed.get("version_corta")
                                decision = parsed.get("decision")
                                score = parsed.get("total_score")
                                formatted_output = short_summary or f"Evaluación R-W-W completada: **{score}/100 → {decision}**"
                        except Exception:
                            pass

                        step.output = formatted_output
                        await step.update()

            # C) Actualización del agente activo
            elif event.type == "agent_updated_stream_event":
                new_agent = getattr(event, "new_agent", None)
                if new_agent and response_msg:
                    response_msg.author = getattr(new_agent, "name", "Galileo")

        # Asegurar remoción de spinner y actualización final del mensaje
        if spin_msg:
            await spin_msg.remove()
        if response_msg:
            await response_msg.update()

    except asyncio.CancelledError:
        logging.info("Ejecución cancelada por el usuario.")
        if spin_msg:
            await spin_msg.remove()
        if response_msg:
            await response_msg.stream_token("\n\n⏹️ *Respuesta detenida por el usuario.*")
            await response_msg.update()
    except Exception as e:
        logging.error(f"Error durante el stream del runner: {e}", exc_info=True)
        if spin_msg:
            await spin_msg.remove()
        await cl.ErrorMessage(content=f"Ocurrió un error inesperado: {str(e)}").send()


# ─────────────────────────────  CANCELACIÓN  ───────────────────────────────────
@cl.on_stop
async def stop_chat():
    """Cancela inmediatamente el runner activo si el usuario pulsa 'Detener'."""
    runner_result = cl.user_session.get("runner_result")
    if runner_result:
        try:
            runner_result.cancel(mode="immediate")
            logging.info("runner_result.cancel() ejecutado con éxito.")
        except Exception as e:
            logging.error(f"Error al cancelar runner: {e}")


if __name__ == "__main__":
    cl.app(
        title="Galileo AI Agent",
        description="Chainlit app para interactuar con Galileo, de MIC Business Consulting.",
        theme="dark",
    )
