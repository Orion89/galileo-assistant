import asyncio
from datetime import datetime, timezone
from functools import partial
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import List

import chainlit as cl
from chainlit import User
from chainlit.config import config
from chainlit.data import get_data_layer
from chainlit.element import Element
from dotenv import load_dotenv
load_dotenv()
from openai import AsyncAssistantEventHandler, AsyncOpenAI, OpenAI, BadRequestError
from openai.types.beta.threads.runs import RunStep

from assistants import config as asst_config
from tools.definitions.download_files import download_external_file
from tools.functions.download_external_file import download_and_attach
from tools.definitions.rww import rww_evaluate as rww_function_def
from tools.functions.rww_evaluate import rww_evaluate as rww_execute

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()],
)
def _handle_exception(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        return sys.__excepthook__(exc_type, exc, tb)
    logging.critical("❌ Excepción no controlada", exc_info=(exc_type, exc, tb))
sys.excepthook = _handle_exception

# ─────────────────────────────  ENV & CONSTANTS  ───────────────────────────────
EMAIL_RX   = r"^[\w\.-]+@[\w\.-]+\.\w{2,4}$"
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
# ASST_ID    = os.getenv("ASSISTANT_ID")
ASST_ID = asst_config.ensure_assistant(assistant_def=asst_config.assistant_def.model_dump(), assistant_id=os.getenv("ASSISTANT_ID"))
VS_ID = os.getenv("VECTOR_STORE_ID")

if not OPENAI_KEY or not ASST_ID:
    raise RuntimeError("Debes definir OPENAI_API_KEY y ASSISTANT_ID.")

# Cliente síncrono solo para metadata
sync_oai  = OpenAI(api_key=OPENAI_KEY)
assistant = sync_oai.beta.assistants.retrieve(ASST_ID)
# Cliente asíncrono para la sesión
oai = AsyncOpenAI(api_key=OPENAI_KEY)
config.ui.name = assistant.name  
# ─────────────────────────────  UTILIDADES  ────────────────────────────────────
FILE_MAP = {
    f.id: f.filename.partition(".pdf")[0]
    for f in sync_oai.files.list().data
    if f.purpose == "assistants"
}
# https://platform.openai.com/docs/assistants/tools/file-search
FS_EXTS = {
    ".txt", ".md", ".rtf",
    ".json", ".yaml", ".yml", ".xml",
    ".html", ".htm",
    ".pdf",
    ".doc", ".docx", ".ppt", ".pptx",
    ".odt", ".odp",
    ".epub",
}
CI_ONLY_EXTS = {
    ".csv", ".tsv", ".tab",
    ".xls", ".xlsx", ".ods",
}

def _extract_answer(msg):
    parts = []
    for item in msg.content:
        if item.type == "text":
            txt = item.text.value
            for ann in item.text.annotations:
                if ann.type == "file_citation":
                    src = ann.file_citation
                    txt += f" [Fuente: {FILE_MAP.get(src.file_id, src.file_id)}]"
            parts.append(txt)
    return "\n\n".join(parts) if parts else "Sin respuesta."


async def _upload_files(elements: List[Element]) -> list[dict]:
    """
    Sube los archivos adjuntos y construye la lista 'attachments' eligiendo la herramienta correcta según el tipo de archivo.
    La herramienta File Search soporta los tipos detallados en: https://platform.openai.com/docs/assistants/tools/file-search#supported-files
    """
    if not elements:
        return []

    attachments, rejected = [], []

    for el in elements:
        ext = Path(el.path).suffix.lower()

        # ───────── Asignar herramientas ─────────
        if ext in FS_EXTS:
            tools = [
                {"type": "code_interpreter"},
                {"type": "file_search"},
            ]
        elif ext in CI_ONLY_EXTS:
            tools = [
                {"type": "code_interpreter"},
            ]
        else:
            rejected.append(el.name)
            continue

        # Subir a OpenAI File Storage
        up = await oai.files.create(file=Path(el.path), purpose="assistants")
        attachments.append({"file_id": up.id, "tools": tools})

    # Avisar al usuario si algún archivo fue omitido
    if rejected:
        await cl.Message(
            "⚠️ Formatos aún no soportados y omitidos: " + ", ".join(rejected)
        ).send()

    return attachments


async def _spinner(msg: cl.Message, stop: dict):
    """Muestra 'Pensando', 'Pensando.' … hasta que stop["done"] sea True."""
    if msg is None:
        return
    dots = ["", ".", "..", "..."]
    i = 0
    while not stop["done"]:
        msg.content = f"🤖 Pensando{dots[i % 4]}"
        await msg.update()
        i += 1
        await asyncio.sleep(0.6)


# ─────────────────────────────  TOOLS  ───────────────────────────────

try:
    sync_oai.beta.assistants.update(
        ASST_ID,
        tools=[
            {"type": "code_interpreter"},
            {"type": "file_search"},
            {"type": "function", "function": download_external_file},
            {"type": "function", "function": rww_function_def},
        ],
        tool_resources={"file_search": {"vector_store_ids": [VS_ID]}},
    )
except BadRequestError as e:
    print(("Assistant update failed: %s", e))
    logging.error("Assistant update failed: %s", e)

download_external_file_partial = partial(download_and_attach, allowed_file_exts=FS_EXTS, code_interpreter_file_exts=CI_ONLY_EXTS, oai_client=oai)
assistant_def_updated = sync_oai.beta.assistants.retrieve(ASST_ID).model_dump()
print(f"Updated assistant definition: {json.dumps(assistant_def_updated, indent=2)}")

class StreamHandler(AsyncAssistantEventHandler):
    def __init__(self, spin_msg: cl.Message, stop_flag: dict, thread_id: str):
        super().__init__()
        self.spin_msg = spin_msg
        self.stop_flag = stop_flag
        self.thread_id = thread_id
        self.run_id = None 
        self.msg:  cl.Message | None = None
        self.step: cl.Step    | None = None
        self.current_tool_id = None # ID de la herramienta actual
    
    async def _latest_open_run(self) -> str | None:
        """
        Devuelve el ID del run más reciente que esté esperando la salida
        de herramientas (requires_action) o en progreso.
        """
        runs = await oai.beta.threads.runs.list(thread_id=self.thread_id, limit=5)
        for run in runs.data:
            if run.status in ("requires_action", "in_progress"):
                return run.id
        return None


    # — texto del asistente —
    async def on_text_created(self, text):
        self.stop_flag["done"] = True
        if self.spin_msg:
            await self.spin_msg.remove()
        self.msg = await cl.Message(author=assistant.name, content="").send()

    async def on_text_delta(self, delta, snapshot):
        await self.msg.stream_token(delta.value)

    async def on_text_done(self, text):
        await self.msg.update()

    async def on_event(self, event):
        # OpenAI envía `event.event = "error"` si algo sale mal en el Run
        if event.event == "error":
            await cl.ErrorMessage(content=str(event.data.message)).send()

    async def on_exception(self, exception: Exception):
        """"Manejo de excepciones no controladas en el asistente."""
        await cl.ErrorMessage(content=str(exception)).send()

    async def on_run_created(self, run):
        self.run_id = run.id

    # — llamadas a herramientas (code_interpreter / file_search) —
    async def on_tool_call_created(self, call):
        """
        1. Si la tool es download_external_file (function) → ejecuta descarga,
           sube a File Storage y responde usando submit_tool_outputs; NO crea Step.
        2. Para cualquier otra tool (code_interpreter, file_search…) →
           crea un cl.Step como antes para mostrar progreso.
        """
        # interceptar llamada a la función personalizada
        if call.type == "function" and call.function.name == "download_external_file":
            try:
                args = json.loads(call.function.arguments)
                url  = args["url"]
            except Exception:
                result = {"error": "invalid_arguments"}
            else:
                result = await download_external_file_partial(url=url) or {"error": "download_failed"}

            run_id = (
                self.run_id
                or getattr(call, "run_id", None)
                or getattr(call, "parent_run_id", None)
            )

            if run_id is None: # último fallback vía API
                runs = await oai.beta.threads.runs.list(thread_id=self.thread_id, limit=10)
                for r in runs.data:
                    if r.status == "requires_action":
                        run_id = r.id
                        break
                    
            if run_id is None: # aún imposible → registra y aborta
                logging.error("run_id sigue ausente en estado requires_action; skip submit_tool_outputs")
                return

            self.run_id = run_id  
            
            # enviar la salida de la función al Assistants API
            await oai.beta.threads.runs.submit_tool_outputs(
                thread_id=self.thread_id,
                run_id=run_id,
                tool_outputs=[{
                    "tool_call_id": call.id,
                    "output": json.dumps(result)
                }],
            )
            return  # no crear Step para esta función

                # Manejo de funcion: rww_evaluate (NUEVO)
        if call.type == "function" and call.function.name == "rww_evaluate":
            # Crear Step visible para rww_evaluate
            rww_step = cl.Step(name="rww_evaluate", type="tool")
            rww_step.start = datetime.now(timezone.utc).isoformat()
            await rww_step.send()

            try:
                args = json.loads(call.function.arguments)  # {"payload": {...}}
                payload = args["payload"]
            except Exception:
                result = json.dumps({"error": "invalid_arguments"}, ensure_ascii=False)
            else:
                try:
                    result_json = rww_execute(payload=payload)  # JSON string
                    result = result_json if isinstance(result_json, str) else json.dumps(result_json, ensure_ascii=False)
                except Exception as ex:
                    result = json.dumps({"error": "execution_failed", "detail": str(ex)}, ensure_ascii=False)

            run_id = (
                self.run_id
                or getattr(call, "run_id", None)
                or getattr(call, "parent_run_id", None)
            )
            if run_id is None:
                runs = await oai.beta.threads.runs.list(thread_id=self.thread_id, limit=10)
                for r in runs.data:
                    if r.status == "requires_action":
                        run_id = r.id
                        break
            if run_id is None:
                logging.error("run_id ausente en requires_action; skip submit_tool_outputs (rww_evaluate)")
                rww_step.language = "markdown"
                rww_step.output = "No se pudo enviar el resultado a OpenAI (run_id no encontrado)."
                rww_step.end = datetime.now(timezone.utc).isoformat()
                await rww_step.update()
                return

            self.run_id = run_id
            await oai.beta.threads.runs.submit_tool_outputs(
                thread_id=self.thread_id,
                run_id=run_id,
                tool_outputs=[{
                    "tool_call_id": call.id,
                    "output": result
                }],
            )

            # Mostrar version_corta en el Step al concluir
            version_corta = None
            try:
                parsed = json.loads(result)
                version_corta = parsed.get("version_corta")
                if not version_corta and parsed.get("decision") is not None:
                    ps = parsed.get("pillar_scores") or []
                    def pc(i):
                        try:
                            return ps[i].get("score_percent")
                        except Exception:
                            return None
                    version_corta = (
                        f"RWW={parsed.get('total_score')}/100 -> {parsed.get('decision')}"
                        + (f". Real {pc(0)} | Win {pc(1)} | Worth {pc(2)}" if ps else "")
                    )
            except Exception:
                version_corta = None

            rww_step.language = "markdown"
            rww_step.output = version_corta or "Evaluacion RWW ejecutada."
            rww_step.end = datetime.now(timezone.utc).isoformat()
            await rww_step.update()
            return        # flujo normal (code_interpreter, file_search, etc.)
        self.current_tool_id = call.id
        self.step = cl.Step(name=call.type, type="tool")
        if call.type == "code_interpreter":
            self.step.show_input = "python"
        self.step.start = datetime.now(timezone.utc).isoformat()
        await self.step.send()

    async def on_tool_call_delta(self, delta, snapshot):
        # Si OpenAI abre otra llamada (p.e. varios code_interpreter) crear otro step
        if snapshot.id != self.current_tool_id:
            self.current_tool_id = snapshot.id
            self.step = cl.Step(name=delta.type, type="tool")
            self.step.start = datetime.now(timezone.utc).isoformat()
            if snapshot.type == "code_interpreter":
                self.step.show_input = "python"
            await self.step.send()

        # --- streaming dentro del mismo step ---
        if delta.type == "code_interpreter":
            # 1️⃣ input (código)
            if delta.code_interpreter.input:
                await self.step.stream_token(delta.code_interpreter.input, is_input=True)

            # 2️⃣ outputs (logs o imágenes)
            if delta.code_interpreter.outputs:
                for output in delta.code_interpreter.outputs:
                    if output.type == "logs":
                        self.step.language = "markdown"
                        self.step.output += output.logs
                        self.step.end = datetime.now(timezone.utc).isoformat()
                        await self.step.update()
                    elif output.type == "image":
                        # guarda JSON con metadatos para que Chainlit lo pinte
                        self.step.language = "json"
                        self.step.output = output.image.model_dump_json()


    async def on_tool_call_done(self, call):
        if getattr(self, "step", None):
            self.step.end = datetime.now(timezone.utc).isoformat()
            await self.step.update()

    async def on_image_file_done(self, image_file):
        img_id = image_file.file_id
        response = await oai.files.with_raw_response.content(img_id)
        image_element = cl.Image(
            name=img_id, content=response.content, display="inline", size="large"
        )
        if not self.msg.elements:
            self.msg.elements = []
        self.msg.elements.append(image_element)
        await self.msg.update()



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

# ─────────────────────────  AUTENTICACIÓN POR CORREO  ──────────────────────────
@cl.password_auth_callback
def auth(email: str, pwd: str | None = None):
    return User(identifier=email, metadata={"email": email}) if re.match(EMAIL_RX, email) else None

# ─────────────────────────────  CICLO DE VIDA  ────────────────────────────────
@cl.on_chat_start
async def on_start():
    user = cl.user_session.get("user")
    meta = dict(user.metadata or {})

    email = meta.get("email") or user.identifier
    display_name = meta.get("name") or email
    # 2persistir e-mail si faltaba (sólo si el helper existe)
    if "email" not in meta:
        meta["email"] = email                    # actualiza en memoria
        user.metadata = meta                     # refleja en sesión

        dl = get_data_layer()
        if dl and hasattr(dl, "update_user_metadata"):
            await dl.update_user_metadata(user.identifier, meta)
    

    welcome = (
        f"👋 **¡Hola {display_name}!**\n\n"
        "Soy **Galileo**, tu agente de IA especializado en innovación.\n\n"
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
        
    thread = await oai.beta.threads.create()
    cl.user_session.set("thread_id", thread.id)

    if dl := get_data_layer():
        if await dl.get_user(user.identifier) is None:
            await dl.create_user(user)
        await dl.update_thread(
            thread_id=cl.context.session.thread_id,
            metadata={"openai_thread_id": thread.id},
        )

@cl.on_chat_resume
async def on_resume(meta):
    if tid := meta.get("metadata", {}).get("openai_thread_id"):
        cl.user_session.set("thread_id", tid)

# ─────────────────────────────  MANEJO DE MENSAJES  ───────────────────────────
@cl.on_message
async def on_message(msg: cl.Message):
    thread_id = cl.user_session.get("thread_id")
    if not thread_id:
        await cl.Message("⚠️ Sesión no inicializada.").send()
        return

    attachments = await _upload_files(msg.elements)

    await oai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=msg.content,
        attachments=attachments,
    )

    thinking = await cl.Message("🤖 Pensando").send()
    stop = {"done": False}
    spinner_task = asyncio.create_task(_spinner(thinking, stop))
    async with oai.beta.threads.runs.stream(
        thread_id=thread_id,
        assistant_id=ASST_ID,
        event_handler=StreamHandler(thinking, stop, thread_id=thread_id),
    ) as stream:
        await stream.until_done()

    stop["done"] = True
    if spinner_task:
        await spinner_task

# cuando un usuario hace clic en "Detener" mientras se ejecuta una tarea
# https://docs.chainlit.io/concepts/chat-lifecycle#on-stop
@cl.on_stop
async def stop_chat():
    run_step = cl.user_session.get("run_step")
    if run_step:
        await oai.beta.threads.runs.cancel(
            thread_id=run_step.thread_id, run_id=run_step.run_id
        )


if __name__ == "__main__":
    cl.app(title="Galileo AI Agent", description="Chainlit app para interactuar con Galileo, de MIC Business Consulting.", theme="dark")