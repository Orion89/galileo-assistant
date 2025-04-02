import asyncio
from datetime import datetime
import json
import os
import re
import time

from config.database import init_db, register_user, save_message, get_user_messages

import chainlit as cl
from chainlit.input_widget import TextInput
import openai
from dotenv import load_dotenv

load_dotenv()
init_db()

EMAIL_REGEX = r"^[\w\.-]+@[\w\.-]+\.\w{2,4}$"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

if not OPENAI_API_KEY or not ASSISTANT_ID:
    raise ValueError("OPENAI_API_KEY and ASSISTANT_ID must be set in the environment variables.")

# Cliente de OpenAI
openai.api_key = OPENAI_API_KEY
FILE_MAP = {f.id: f.filename.partition(".pdf")[0] for f in openai.files.list().data if f.purpose == "assistants"}

# Manejo de archivos por usuario
def get_user_data(user_id):
    user_folder = os.path.join("conversations", user_id)
    filename = os.path.join(user_folder, "thread.json")
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"thread_id": None, "messages": []}

def save_user_data(user_id, thread_id, messages):
    user_folder = os.path.join("conversations", user_id)
    os.makedirs(user_folder, exist_ok=True)
    filename = os.path.join(user_folder, "thread.json")
    data = {"thread_id": thread_id, "messages": messages}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Procesar contenido del Assistant con citas
def extract_message_content(message):
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

# Animación de "pensando..."
async def animate_thinking(message, stop_flag):
    dots = [".", "..", "..."]
    i = 0
    while not stop_flag["done"]:
        message.content = f"🤖 Pensando{dots[i % 3]}"
        await message.update()
        i += 1
        await asyncio.sleep(0.6)


@cl.on_chat_start
async def on_chat_start():
    while True:
        res_email = await cl.AskUserMessage("📧 Ingresa tu correo electrónico para comenzar:").send()
        if not res_email or res_email.get("output") is None:
            await cl.Message(content="❌ No se recibió un correo. Intenta nuevamente.").send()
            continue

        email = res_email["output"].strip()
        if re.match(EMAIL_REGEX, email):
            break
        else:
            await cl.Message(content="❌ El formato del correo no es válido. Intenta nuevamente.").send()

    # Guardar sesión y registrar usuario
    cl.user_session.set("user_id", email)
    register_user(email)

    # Mostrar historial
    history = get_user_messages(email)
    if history:
        await cl.Message(content="📜 Recuperando tu historial anterior...").send()
        for role, content, ts in history:
            await cl.Message(author=role, content=content).send()
    else:
        await cl.Message(content="👋 ¡Bienvenido a Galileo! Puedes comenzar tu conversación.").send()


@cl.on_message
async def main(message: cl.Message):
    # Recuperar el correo electrónico como user_id
    user_email = cl.user_session.get("user_id")
    if not user_email:
        await cl.Message(content="⚠️ Usuario no autenticado.").send()
        return

    user_input = message.content
    start_time = time.time()

    # Mostrar mensaje de espera
    thinking_msg = await cl.Message(content="🤖 Pensando.").send()
    stop_flag = {"done": False}
    animation_task = asyncio.create_task(animate_thinking(thinking_msg, stop_flag))

    # Crear un nuevo thread
    thread = openai.beta.threads.create()
    thread_id = thread.id

    # Agregar mensaje del usuario
    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_input
    )

    # Crear ejecución
    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    # Esperar respuesta
    while True:
        run_status = openai.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id
        )
        if run_status.status in ["completed", "failed", "cancelled"]:
            break
        await asyncio.sleep(1)

    if run_status.status != "completed":
        stop_flag["done"] = True
        await animation_task
        thinking_msg.content = "⚠️ Ocurrió un error al generar la respuesta."
        await thinking_msg.update()
        return

    # Obtener la respuesta
    messages_response = openai.beta.threads.messages.list(thread_id=thread_id)
    assistant_messages = [msg for msg in messages_response.data if msg.role == "assistant"]
    bot_reply = extract_message_content(assistant_messages[0]) if assistant_messages else "Sin respuesta."

    stop_flag["done"] = True
    await animation_task

    # Guardar mensajes en base de datos
    response_time = round(time.time() - start_time, 2)
    save_message(user_email, role="user", content=user_input)
    save_message(user_email, role="assistant", content=bot_reply, response_time=response_time)

    # Mostrar respuesta en interfaz
    thinking_msg.content = bot_reply
    await thinking_msg.update()

# Configuración de la app Chainlit
if __name__ == "__main__":
    cl.app(
        title="Galileo OpenAI Assistant",
        description="Demo para interactuar con Galileo.",
        theme="dark"
    )