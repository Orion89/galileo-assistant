# import sqlite3
import chainlit as cl
from datetime import datetime

# DB_PATH = "galileo_chat.db"

def init_db():
    # Con la Official Data Layer de Chainlit no se requiere inicialización.
    pass

def register_user(email: str):
    now = datetime.utcnow().isoformat()
    user_key = f"user:{email}"
    user_data = cl.data.get(user_key)
    if not user_data:
        user_data = {
            "email": email,
            "created_at": now,
            "last_active": now,
            "messages": []
        }
    else:
        user_data["last_active"] = now
    cl.data.set(user_key, user_data)

def save_message(user_email: str, role: str, content: str, response_time: float = None):
    now = datetime.utcnow().isoformat()
    message = {
        "role": role,
        "content": content,
        "timestamp_utc": now,
        "response_time_seconds": response_time
    }
    user_key = f"user:{user_email}"
    user_data = cl.data.get(user_key)
    if not user_data:
        user_data = {
            "email": user_email,
            "created_at": now,
            "last_active": now,
            "messages": []
        }
    if "messages" not in user_data:
        user_data["messages"] = []
    user_data["messages"].append(message)
    cl.data.set(user_key, user_data)

def get_user_messages(user_email: str):
    user_key = f"user:{user_email}"
    user_data = cl.data.get(user_key)
    if user_data:
        return user_data.get("messages", [])
    return []