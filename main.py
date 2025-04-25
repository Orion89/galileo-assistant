import asyncio
import logging
import os
import re
import sys

from dotenv import load_dotenv
import chainlit as cl
from chainlit import User
from chainlit.data import get_data_layer
import openai

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(nivelname)s - %(message)s",
    handlers=[
        logging.FileHandler("app.log"),  # Archivo de logs
        logging.StreamHandler()         # Consola
    ]
)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.critical("Excepción no controlada", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

load_dotenv()

# --------------------------------------------------------------------------------------
# Configuración básica
# --------------------------------------------------------------------------------------
EMAIL_REGEX = r"^[\w\.-]+@[\w\.-]+\.\w{2,4}$"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

if not OPENAI_API_KEY or not ASSISTANT_ID:
    raise RuntimeError("Definir OPENAI_API_KEY y ASSISTANT_ID en el entorno.")

openai.api_key = OPENAI_API_KEY

# --------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------
FILE_MAP = {f.id: f.filename.partition(".pdf")[0] for f in openai.files.list().data if f.purpose == "assistants"}

def extract_message_content(message):
    parts = []
    for item in message.content:
        if item.type == "text":
            txt = item.text.value
            for ann in item.text.annotations:
                if ann.type == "file_citation":
                    src = ann.file_citation
                    txt += f" [Fuente: {FILE_MAP.get(src.file_id, src.file_id)}]"
            parts.append(txt)
    return "\n\n".join(parts) if parts else "Sin respuesta."


async def spinner(msg, flag):
    dots = [".", "..", "..."]
    i = 0
    while not flag["done"]:
        msg.content = f"🤖 Pensando{dots[i%3]}"
        await msg.update()
        i += 1
        await asyncio.sleep(0.6)

# --------------------------------------------------------------------------------------
# Autenticación
# --------------------------------------------------------------------------------------
@cl.password_auth_callback
def email_auth(user: str, pwd: str | None = None):
    if re.match(EMAIL_REGEX, user):
        logging.info(f"Usuario autenticado: {user}")
        return User(identifier=user, metadata={"email": user})
    logging.warning(f"Intento de autenticación fallido para: {user}")
    return None

# --------------------------------------------------------------------------------------
# Hooks ciclo de vida
# --------------------------------------------------------------------------------------
@cl.on_chat_start
async def chat_start():
    user: User = cl.user_session.get("user")
    logging.info(f"Inicio de chat para usuario: {user.metadata['email']}")
    await cl.Message(f"👋 ¡Hola **{user.metadata['email']}**! Ya puedes comenzar.").send()

    if dl := get_data_layer():
        if await dl.get_user(user.identifier) is None:
            await dl.create_user(user)


@cl.on_chat_resume
async def chat_resume(thread):  # ThreadDict
    logging.info(f"Reanudando chat con metadata: {thread.get('metadata', {})}")
    if tid := thread.get("metadata", {}).get("openai_thread_id"):
        cl.user_session.set("openai_thread_id", tid)
    #for m in cl.chat_history:
    #    await cl.Message(author=m.author, content=m.content).send()


@cl.on_message
async def handle(msg: cl.Message):
    logging.info(f"Mensaje recibido: {msg.content}")
    if cl.user_session.get("user") is None:
        await cl.Message("⚠️ Usuario no autenticado.").send(); return

    thinking = await cl.Message("🤖 Pensando").send()
    flag = {"done": False}
    anim = asyncio.create_task(spinner(thinking, flag))

    # --- Hilo OpenAI ------------------------------------------------------------------
    openai_tid = cl.user_session.get("openai_thread_id")
    if not openai_tid:
        try:
            openai_tid = openai.beta.threads.create().id
            cl.user_session.set("openai_thread_id", openai_tid)
            logging.info(f"Nuevo thread_id creado: {openai_tid}")
        except Exception as e:
            logging.error(f"Error al crear thread_id: {e}")
            await cl.Message("⚠️ Error al iniciar hilo de OpenAI.").send()
            return

    # Persistimos el mapping en el Data Layer                             
    if dl := get_data_layer():
        await dl.update_thread(
            thread_id=cl.context.session.thread_id,
            metadata={"openai_thread_id": openai_tid},
        )

    # Llamada a OpenAI
    try:
        openai.beta.threads.messages.create(thread_id=openai_tid, role="user", content=msg.content)
        logging.info(f"Mensaje enviado a OpenAI: {msg.content}")
        run = openai.beta.threads.runs.create(thread_id=openai_tid, assistant_id=ASSISTANT_ID)
        logging.info(f"Run iniciado: {run.id}")
    except Exception as e:
        logging.error(f"Error al enviar mensaje a OpenAI: {e}")
        flag["done"] = True
        await anim
        await thinking.update(content="⚠️ Error al generar respuesta.")
        return

    while True:
        status = openai.beta.threads.runs.retrieve(thread_id=openai_tid, run_id=run.id)
        logging.info(f"Estado del run {run.id}: {status.status}")
        if status.status in {"completed", "failed", "cancelled"}:
            break
        await asyncio.sleep(1)

    if status.status != "completed":
        logging.error(f"Run {run.id} fallido con estado: {status.status}")
        flag["done"] = True
        await anim
        await thinking.update(content="⚠️ Error al generar respuesta.")
        return

    msgs = openai.beta.threads.messages.list(thread_id=openai_tid).data
    reply = extract_message_content(next(m for m in msgs if m.role == "assistant"))
    logging.info(f"Respuesta generada por OpenAI: {reply}")

    flag["done"] = True; await anim; await thinking.remove()
    await cl.Message(reply).send()

# --------------------------------------------------------------------------------------
if __name__ == "__main__":
    cl.app(title="Galileo OpenAI Assistant", description="Chainlit app para interactuar con Galileo, de MIC Business Consulting.", theme="dark")