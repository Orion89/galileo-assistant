import asyncio
from datetime import datetime
import json
import os
import time

import chainlit as cl
import openai
from dotenv import load_dotenv

load_dotenv()


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

if not OPENAI_API_KEY or not ASSISTANT_ID:
    raise ValueError("OPENAI_API_KEY and ASSISTANT_ID must be set in the environment variables.")

# Cliente de OpenAI
openai.api_key = OPENAI_API_KEY
FILE_MAP = {f.id: f.filename.partition(".pdf")[0] for f in openai.files.list().data if f.purpose == "assistants"}

def get_user_data(user_id):
    """Obtiene los datos del usuario desde su carpeta individual."""
    user_folder = os.path.join("conversations", user_id)
    filename = os.path.join(user_folder, "thread.json")
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"thread_id": None, "messages": []}

def save_user_data(user_id, thread_id, messages):
    """Guarda los datos del usuario en su propia carpeta."""
    user_folder = os.path.join("conversations", user_id)
    os.makedirs(user_folder, exist_ok=True)
    filename = os.path.join(user_folder, "thread.json")
    data = {"thread_id": thread_id, "messages": messages}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def extract_message_content(message):
    parts = []
    for item in message.content:
        if item.type == "text":
            text = item.text.value
            for annotation in item.text.annotations:
                if annotation.type == "file_citation":
                    source = annotation.file_citation
                    file_name = FILE_MAP.get(source.file_id, source.file_id)
                    citation_text = f" [Fuente: {file_name}]"
                    # citation_text = f" [Fuente: archivo {source.file_id}]" if hasattr(source, "file_id") else " [Fuente]"
                    text += citation_text
            parts.append(text)
    return "\n\n".join(parts) if parts else "Sin respuesta."


@cl.on_message
async def main(message: cl.Message):
    user_id = message.author
    user_input = message.content

    # Mostrar mensaje "Pensando..."
    thinking_msg = await cl.Message(content="🤖 Pensando.").send()
    keep_animating = True

    async def animate_thinking():
        dots = [".", "..", "..."]
        i = 0
        while keep_animating:
            thinking_msg.content = f"🤖 Pensando{dots[i % 3]}"
            await thinking_msg.update()
            i += 1
            await asyncio.sleep(0.6)

    animation_task = asyncio.create_task(animate_thinking())


    user_data = get_user_data(user_id)
    thread_id = user_data.get("thread_id")
    messages = user_data.get("messages", [])

    # Crear thread si no existe
    if thread_id is None:
        thread = openai.beta.threads.create()
        thread_id = thread.id

    # Agregar mensaje del usuario al thread
    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_input
    )

    # Iniciar run del Assistant
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

    # Si falla, mostrar mensaje de error
    if run_status.status != "completed":
        keep_animating = False
        await animation_task
        thinking_msg.content = "⚠️ Ocurrió un error al generar la respuesta."
        await thinking_msg.update()
        return

    # Extraer respuesta del Assistant
    messages_response = openai.beta.threads.messages.list(thread_id=thread_id)
    assistant_messages = [msg for msg in messages_response.data if msg.role == "assistant"]
    bot_reply = extract_message_content(assistant_messages[0]) if assistant_messages else "Sin respuesta."

    # Guardar conversación
    messages.append({"role": "user", "content": user_input})
    messages.append({"role": "assistant", "content": bot_reply})
    save_user_data(user_id, thread_id, messages)

    keep_animating = False
    await animation_task
    thinking_msg.content = bot_reply
    await thinking_msg.update()


if __name__ == "__main__":
    cl.app(title="Galileo OpenAI Assistant", description="Demo para interactuar con Galileo.", theme="dark")