# main.py  –  Chainlit 2.4.400 · Streaming + Files + Code-Interpreter + File-Search
import asyncio
from datetime import datetime, timezone
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import chainlit as cl
from chainlit import User
from chainlit.data import get_data_layer
from chainlit.element import Element
from dotenv import load_dotenv
from openai import AsyncAssistantEventHandler, AsyncOpenAI, OpenAI

# ────────────────────────────────  LOGGING  ────────────────────────────────────
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
load_dotenv()
EMAIL_RX   = r"^[\w\.-]+@[\w\.-]+\.\w{2,4}$"
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
ASST_ID    = os.getenv("ASSISTANT_ID")
if not OPENAI_KEY or not ASST_ID:
    raise RuntimeError("Debes definir OPENAI_API_KEY y ASSISTANT_ID.")

# Cliente síncrono solo para metadata
sync_oai  = OpenAI(api_key=OPENAI_KEY)
assistant = sync_oai.beta.assistants.retrieve(ASST_ID)

# Cliente asíncrono para la sesión
oai = AsyncOpenAI(api_key=OPENAI_KEY)

# ─────────────────────────────  UTILIDADES  ────────────────────────────────────
FILE_MAP = {
    f.id: f.filename.partition(".pdf")[0]
    for f in sync_oai.files.list().data
    if f.purpose == "assistants"
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
    """Sube archivos adjuntos y construye la lista 'attachments' para la API."""
    if not elements:
        return []
    file_ids = []
    for el in elements:
        up = await oai.files.create(file=Path(el.path), purpose="assistants")
        file_ids.append(up.id)
    return [
        {
            "file_id": fid,
            "tools": [{"type": "code_interpreter"}, {"type": "file_search"}],
        }
        for fid in file_ids
    ]

# ─────────────────────────────  STREAM HANDLER  ────────────────────────────────
class StreamHandler(AsyncAssistantEventHandler):
    def __init__(self):
        super().__init__()
        self.msg:  cl.Message | None = None
        self.step: cl.Step    | None = None

    # — texto del asistente —
    async def on_text_created(self, text):
        self.msg = await cl.Message(author=assistant.name, content="").send()

    async def on_text_delta(self, delta, snapshot):
        await self.msg.stream_token(delta.value)

    async def on_text_done(self, text):
        await self.msg.update()

    # — llamadas a herramientas (code_interpreter / file_search) —
    async def on_tool_call_created(self, call):
        self.step = cl.Step(name=call.type, type="tool")
        self.step.language = "python"
        # Marca opcional: hora de inicio en UTC
        self.step.start = datetime.now(timezone.utc).isoformat()
        await self.step.send()

    async def on_tool_call_delta(self, delta, snapshot):
        if delta.type == "code_interpreter" and delta.code_interpreter.input:
            await self.step.stream_token(delta.code_interpreter.input)

    async def on_tool_call_done(self, call):
        # Marca opcional: hora de término en UTC
        self.step.end = datetime.now(timezone.utc).isoformat()
        await self.step.update()

# ─────────────────────────  AUTENTICACIÓN POR CORREO  ──────────────────────────
@cl.password_auth_callback
def auth(email: str, pwd: str | None = None):
    return User(identifier=email, metadata={"email": email}) if re.match(EMAIL_RX, email) else None

# ─────────────────────────────  CICLO DE VIDA  ────────────────────────────────
@cl.on_chat_start
async def on_start():
    user: User = cl.user_session.get("user")
    await cl.Message(f"👋 ¡Hola **{user.metadata['email']}**!").send()

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
        await cl.Message("⚠️ Sesión no inicializada.").send(); return

    attachments = await _upload_files(msg.elements)

    await oai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=msg.content,
        attachments=attachments,
    )

    async with oai.beta.threads.runs.stream(
        thread_id=thread_id,
        assistant_id=ASST_ID,
        event_handler=StreamHandler(),
    ) as stream:
        await stream.until_done()


if __name__ == "__main__":
    cl.app(title="Galileo OpenAI Assistant", description="Chainlit app para interactuar con Galileo, de MIC Business Consulting.", theme="dark")