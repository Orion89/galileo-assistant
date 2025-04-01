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

# Lógica principal
@cl.on_message
async def main(message: cl.Message):
    user_id = message.author
    user_input = message.content
    utc_time = datetime.utcnow().isoformat()
    start_time = time.time()

    thinking_msg = await cl.Message(content="🤖 Pensando.").send()
    stop_flag = {"done": False}
    animation_task = asyncio.create_task(animate_thinking(thinking_msg, stop_flag))

    user_data = get_user_data(user_id)
    thread_id = user_data.get("thread_id")
    messages = user_data.get("messages", [])

    if thread_id is None:
        thread = openai.beta.threads.create()
        thread_id = thread.id

    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_input
    )

    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

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

    messages_response = openai.beta.threads.messages.list(thread_id=thread_id)
    assistant_messages = [msg for msg in messages_response.data if msg.role == "assistant"]
    bot_reply = extract_message_content(assistant_messages[0]) if assistant_messages else "Sin respuesta."

    end_time = time.time()
    elapsed_time = round(end_time - start_time, 2)

    messages.append({
        "role": "user",
        "content": user_input,
        "timestamp_utc": utc_time
    })
    messages.append({
        "role": "assistant",
        "content": bot_reply,
        "timestamp_utc": datetime.utcnow().isoformat(),
        "response_time_seconds": elapsed_time
    })

    save_user_data(user_id, thread_id, messages)

    stop_flag["done"] = True
    await animation_task
    thinking_msg.content = bot_reply
    await thinking_msg.update()

# Configuración de la app Chainlit
if __name__ == "__main__":
    cl.app(
        title="Galileo OpenAI Assistant",
        description="Demo para interactuar con Galileo.",
        theme="dark"
    )