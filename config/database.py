import sqlite3
from datetime import datetime

DB_PATH = "galileo_chat.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            created_at TEXT,
            last_active TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            role TEXT,
            content TEXT,
            timestamp_utc TEXT,
            response_time_seconds REAL,
            FOREIGN KEY (user_email) REFERENCES users(email)
        )
    ''')
    conn.commit()
    conn.close()

def register_user(email):
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT email FROM users WHERE email = ?', (email,))
    if not c.fetchone():
        c.execute('INSERT INTO users VALUES (?, ?, ?)', (email, now, now))
    else:
        c.execute('UPDATE users SET last_active = ? WHERE email = ?', (now, email))
    conn.commit()
    conn.close()

def save_message(user_email, role, content, response_time=None):
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO messages (user_email, role, content, timestamp_utc, response_time_seconds)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_email, role, content, now, response_time))
    conn.commit()
    conn.close()

def get_user_messages(user_email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT role, content, timestamp_utc FROM messages
        WHERE user_email = ?
        ORDER BY id ASC
    ''', (user_email,))
    messages = c.fetchall()
    conn.close()
    return messages