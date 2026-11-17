#!/usr/bin/env python3
"""Migrate existing SQLite messages/users into Supabase."""
import os, sqlite3, json
from dotenv import load_dotenv
load_dotenv()

DB_PATH = os.environ.get("DB_PATH", "line_messages.db")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")

if not (SUPABASE_URL and SUPABASE_KEY):
    raise SystemExit("Set SUPABASE_URL and SUPABASE_KEY first")

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)
MT = os.environ.get("SUPABASE_TABLE", "messages")
UT = os.environ.get("SUPABASE_USERS_TABLE", "users")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
msgs = [dict(r) for r in conn.execute("SELECT * FROM messages").fetchall()]
def table_exists(c, name):
    return c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
users = [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()] if table_exists(conn, "users") else []
conn.close()

for m in msgs:
    try:
        sb.table(MT).insert({
            "received_at": m["received_at"],
            "user_id": m["user_id"],
            "group_id": m["group_id"],
            "room_id": m["room_id"],
            "event_type": m["event_type"],
            "message_type": m["message_type"],
            "text": m["text"],
            "raw_json": json.loads(m["raw_json"]) if m.get("raw_json") else None,
        }).execute()
    except Exception as e:
        print("msg err", e)

for u in users:
    try:
        sb.table(UT).upsert({
            "user_id": u["user_id"],
            "display_name": u["display_name"],
            "picture_url": u["picture_url"],
            "status_message": u["status_message"],
            "updated_at": u["updated_at"],
        }).execute()
    except Exception as e:
        print("user err", e)

print(f"Migrated {len(msgs)} messages, {len(users)} users")
