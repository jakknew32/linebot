#!/usr/bin/env python3
"""
LINE Bot - อ่านข้อความใน LINE (Webhook)
=======================================
บอทที่รับข้อความจากผู้ใช้ผ่าน LINE Messaging API Webhook
- ตรวจสอบลายเซ็น (X-Line-Signature)
- บันทึกข้อความทั้งหมดลง SQLite
- ตอบกลับผู้ใช้ (echo / ยืนยันการรับ)
- มี endpoint สำหรับอ่านข้อความที่บันทึกไว้ (/messages)

ตั้งค่า Environment Variables:
  LINE_CHANNEL_SECRET       = Channel Secret
  LINE_CHANNEL_ACCESS_TOKEN = Channel Access Token (Long-lived)
"""
import os
import json
import hmac
import hashlib
import base64
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

DB_PATH = os.environ.get("DB_PATH", "line_messages.db")


# ---------- Database ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            user_id TEXT,
            group_id TEXT,
            room_id TEXT,
            event_type TEXT,
            message_type TEXT,
            text TEXT,
            raw_json TEXT
        )"""
    )
    conn.commit()
    conn.close()


init_db()


def save_message(event):
    src = event.get("source", {}) or {}
    msg = event.get("message", {}) or {}
    conn = get_db()
    conn.execute(
        """INSERT INTO messages
           (received_at, user_id, group_id, room_id, event_type, message_type, text, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            src.get("userId"),
            src.get("groupId"),
            src.get("roomId"),
            event.get("type"),
            msg.get("type"),
            msg.get("text"),
            json.dumps(event, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()


# ---------- LINE helpers ----------
def verify_signature(body: bytes, signature: str) -> bool:
    if not CHANNEL_SECRET or not signature:
        return False
    digest = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def reply_message(reply_token, messages):
    if not CHANNEL_ACCESS_TOKEN:
        return False
    url = "https://api.line.me/v2/bot/message/reply"
    payload = {"replyToken": reply_token, "messages": messages}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {CHANNEL_ACCESS_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print("reply error:", e.code, e.read().decode("utf-8", "replace"))
        return False
    except Exception as e:
        print("reply error:", e)
        return False


def push_message(to, messages):
    if not CHANNEL_ACCESS_TOKEN:
        return False
    url = "https://api.line.me/v2/bot/message/push"
    payload = {"to": to, "messages": messages}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {CHANNEL_ACCESS_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print("push error:", e)
        return False


# ---------- Webhook ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()
    if not verify_signature(body, signature):
        return jsonify({"error": "invalid signature"}), 400

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return jsonify({"error": "invalid json"}), 400

    for event in payload.get("events", []):
        save_message(event)

        if event.get("type") == "message":
            reply_token = event.get("replyToken")
            msg = event.get("message", {}) or {}
            mtype = msg.get("type")
            src = event.get("source", {}) or {}
            user_id = src.get("userId")

            if mtype == "text":
                text = msg.get("text", "")
                print(f"[MSG] user={user_id} text={text!r}")
                reply_message(reply_token, [
                    {"type": "text", "text": f"ได้รับข้อความแล้ว: {text}"}
                ])
            elif mtype in ("image", "video", "audio", "file"):
                reply_message(reply_token, [
                    {"type": "text", "text": f"ได้รับไฟล์ประเภท {mtype} แล้ว"}
                ])
            elif mtype == "sticker":
                reply_message(reply_token, [
                    {"type": "text", "text": "ได้รับสติกเกอร์แล้ว 👍"}
                ])
            else:
                reply_message(reply_token, [
                    {"type": "text", "text": "ได้รับข้อความแล้ว"}
                ])

        elif event.get("type") == "follow":
            reply_token = event.get("replyToken")
            reply_message(reply_token, [
                {"type": "text", "text": "ขอบคุณที่เพิ่มเพื่อน! ส่งข้อความมาได้เลยครับ"}
            ])

    return "OK", 200


# ---------- Read messages ----------
@app.route("/messages", methods=["GET"])
def messages():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    q = request.args.get("q", "").strip()
    conn = get_db()
    if q:
        rows = conn.execute(
            "SELECT * FROM messages WHERE text LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (f"%{q}%", limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()

    if request.args.get("format") != "json":
        html = """
        <!doctype html><html><head><meta charset="utf-8"><title>LINE Messages</title>
        <style>body{font-family:sans-serif;margin:20px}table{border-collapse:collapse;width:100%}
        th,td{border:1px solid #ddd;padding:8px;font-size:13px}th{background:#06c755;color:#fff}
        tr:nth-child(even){background:#f9f9f9}</style></head><body>
        <h2>📩 ข้อความที่บอทได้รับ ({{total}} รายการ)</h2>
        <form method="get"><input name="q" placeholder="ค้นหา..." value="{{q}}">
        <button>ค้นหา</button></form><br>
        <table><tr><th>ID</th><th>เวลา</th><th>User</th><th>ประเภท</th><th>ข้อความ</th></tr>
        {% for r in rows %}
        <tr><td>{{r['id']}}</td><td>{{r['received_at']}}</td><td>{{r['user_id']}}</td>
        <td>{{r['message_type'] or r['event_type']}}</td><td>{{r['text']}}</td></tr>
        {% endfor %}
        </table></body></html>
        """
        return render_template_string(html, rows=rows, total=total, q=q)

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "messages": [dict(r) for r in rows],
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})


@app.route("/", methods=["GET"])
def index():
    return "LINE Bot reader is running. POST /webhook, GET /messages"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)