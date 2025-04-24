import asyncio
from datetime import datetime
import json
import os
import re
import time

# from config.database import init_db, register_user, save_message, get_user_messages

import chainlit as cl
from chainlit import User
from chainlit.data import get_data_layer
from chainlit.input_widget import TextInput
import openai
from dotenv import load_dotenv

load_dotenv()

EMAIL_REGEX = r"^[\w\.-]+@[\w\.-]+\.\w{2,4}$"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
openai.api_key = OPENAI_API_KEY
FILE_MAP = {f.id: f.filename.partition(".pdf")[0] for f in openai.files.list().data if f.purpose == "assistants"}

if not OPENAI_API_KEY or not ASSISTANT_ID:
    raise ValueError("OPENAI_API_KEY and ASSISTANT_ID must be set in the environment variables.")

def extract_message_content(message):
    """Extrae el contenido de la respuesta del Assistant, incorporando citas si las hay."""
    parts = []
    for item in message.content:
        if item.type == "text":
            text = item.text.value
            for annotation in item.text.annotations:
                if annotation.type == "file_citation":
                    source = annotation.file_citation
                    file_name = FILE_MAP.get(source.file_id, source.file_id)
                    text += f" [Fuente: {file_name}]"
            parts.append(text)
    return "\n\n".join(parts) if parts else "Sin respuesta."

async def animate_thinking(message, stop_flag):
    """Animación sencilla para indicar al usuario que se está procesando la respuesta."""
    dots = [".", "..", "..."]
    i = 0
    while not stop_flag["done"]:
        message.content = f"🤖 Pensando{dots[i % 3]}"
        await message.update()
        i += 1
        await asyncio.sleep(0.6)


@cl.password_auth_callback
def email_only_auth(username: str, password: str | None = None):
    """
    Accept any well-formed e-mail as `username` and ignore `password`.
    Returning a `cl.User` authenticates the session so chat-history works.
    """
    if re.match(EMAIL_REGEX, username):
        return User(identifier=username, metadata={"email": username})
    return None


@cl.on_chat_start
async def on_chat_start():
    user: User = cl.user_session.get("user")          # creado por el callback
    await cl.Message(
        f"👋 ¡Hola **{user.metadata['email']}**! Ya puedes comenzar."
    ).send()
    cl.user_session.set("email", user.identifier)
    # --- PERSISTENCIA OPCIONAL DEL USUARIO -----------------------------
    dl = get_data_layer()
    if dl:
        persisted = await dl.get_user(user.identifier)
        if persisted is None:
            await dl.create_user(user)


@cl.on_chat_resume
async def on_chat_resume():
    # Restore OpenAI thread for continuity
    metadata = thread.get("metadata", {})
    if "openai_thread_id" in metadata:
        cl.user_session.set("thread_id", metadata["openai_thread_id"])

    # Re-display history in the UI
    for msg in cl.chat_history:
        await cl.Message(author=msg.author, content=msg.content).send()

@cl.on_message
async def main(message: cl.Message):
    """
    Maneja la interacción con el usuario.
    Se conecta al Assistant ya creado, se actualiza el mensaje de 'pensando' y se muestra
    la respuesta. Los mensajes se almacenan utilizando la Official Data Layer con sus campos estándar.
    """
    if cl.user_session.get("user") is None:
        await cl.Message("⚠️ Usuario no autenticado.").send()
        return
    user_input = message.content
    # start_time = time.time()
    # Se muestra mensaje de "pensando"
    user_input = message.content
    thinking = await cl.Message("🤖 Pensando").send()
    stop = {"done": False}
    anim = asyncio.create_task(animate_thinking(thinking, stop))

    # Se crea un nuevo thread en OpenAI para la conversación
    if not cl.user_session.get("thread_id"):
        thread_id = openai.beta.threads.create().id
        cl.user_session.set("thread_id", thread_id)
    thread_id = cl.user_session.get("thread_id")

    if not cl.context.current_thread.metadata.get("openai_thread_id"):
        cl.context.current_thread.metadata["openai_thread_id"] = thread_id

    # Se añade el mensaje del usuario
    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_input
    )

    # Se inicia la ejecución del Assistant
    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    # Se espera hasta que el run se complete
    while True:
        run_status = openai.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id
        )
        if run_status.status in ["completed", "failed", "cancelled"]:
            break
        await asyncio.sleep(1)

    if run_status.status != "completed":
        stop["done"] = True
        await anim
        thinking.content = "⚠️ Ocurrió un error al generar la respuesta."
        await thinking.update()
        return

    # Se obtiene y procesa la respuesta del Assistant
    messages_response = openai.beta.threads.messages.list(thread_id=thread_id)
    assistant_messages = [msg for msg in messages_response.data if msg.role == "assistant"]
    bot_reply = extract_message_content(assistant_messages[0]) if assistant_messages else "Sin respuesta."

    stop["done"] = True
    await anim
    thinking.content = bot_reply
    thinking.author = "assistant"               # ⬅️  ¡clave para reconstruir la UI!
    await thinking.delete()                    # optional: remove spinner
    await cl.Message(bot_reply).send()         # persisted as assistant step

# Configuración de la app Chainlit
if __name__ == "__main__":
    cl.app(
        title="Galileo OpenAI Assistant",
        description="Demo para interactuar con Galileo.",
        theme="dark"
    )