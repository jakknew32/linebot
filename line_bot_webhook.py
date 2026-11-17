#!/usr/bin/env python3
"""
LINE Bot - อ่านข้อความใน LINE (Webhook) + Supabase
===================================================
- ตรวจสอบลายเซ็น (X-Line-Signature)
- บันทึกข้อความทั้งหมดลง Supabase (Postgres) หรือ SQLite (fallback)
- เก็บโปรไฟล์ผู้ใช้ (display name)
- ตอบกลับผู้ใช้ (echo / ยืนยันการรับ)
- Dashboard / สถิติ / export CSV / ประวัติต่อ user
"""
import os
import json
import hmac
import hashlib
import base64
import sqlite3
import csv
import io
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from flask import Flask, request, jsonify, render_template_string, Response

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

DB_PATH = os.environ.get("DB_PATH", "line_messages.db")

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "messages")
SUPABASE_USERS_TABLE = os.environ.get("SUPABASE_USERS_TABLE", "users")

# Google Sheets (รับออเดอร์)
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_WORKSHEET = os.environ.get("GOOGLE_WORKSHEET", "Orders")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_sheets_ws = None
_supabase = None
_last_db_error = None


def use_supabase():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def get_supabase():
    global _supabase, _last_db_error
    if _supabase is None and use_supabase():
        try:
            from supabase import create_client
            _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            print("supabase init error:", e)
            _last_db_error = f"init: {e}"
            _supabase = False
    return _supabase if _supabase else None


# ---------- Google Sheets ----------
def get_sheets_worksheet():
    global _sheets_ws
    if _sheets_ws is not None:
        return _sheets_ws
    if not GOOGLE_SHEET_ID:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        if GOOGLE_SERVICE_ACCOUNT_JSON:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            creds = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
        else:
            creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SHEETS_SCOPES)
        client = gspread.authorize(creds)
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            ws = sh.worksheet(GOOGLE_WORKSHEET)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=GOOGLE_WORKSHEET, rows=1000, cols=10)
            ws.append_row(["received_at", "user_id", "group_id", "room_id", "message_type", "text"])
        _sheets_ws = ws
        return ws
    except Exception as e:
        print("sheets init error:", e)
        return None


def append_order_to_sheet(row):
    ws = get_sheets_worksheet()
    if ws is None:
        return False
    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        print("sheets append error:", e)
        return False


# ---------- Database ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    if use_supabase():
        return
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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT,
            picture_url TEXT,
            status_message TEXT,
            updated_at TEXT
        )"""
    )
    # migrate: add parsed order columns if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    for c in ["store_code", "store_name", "branch", "delivery_date", "items", "phone", "note"]:
        if c not in cols:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {c} TEXT")
    conn.commit()
    conn.close()


init_db()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def get_line_profile(user_id):
    if not user_id or not CHANNEL_ACCESS_TOKEN:
        return {}
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {CHANNEL_ACCESS_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("profile error:", e)
        return {}


# ---------- Data access ----------
def is_phone_line(s):
    s = s.strip()
    if not s:
        return False
    cleaned = s.replace(" ", "").replace("-", "").replace("+", "")
    return cleaned.isdigit() and len(cleaned) >= 9


def parse_order_text(text):
    result = {
        "store_code": None,
        "store_name": None,
        "branch": None,
        "delivery_date": None,
        "items": [],
        "phone": None,
        "note": None,
    }
    if not text:
        return result
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    if not lines:
        return result
    if len(lines) >= 1:
        result["store_code"] = lines[0]
    if len(lines) >= 2:
        line2 = lines[1]
        result["store_name"] = line2
        if " - " in line2:
            parts = line2.split(" - ", 1)
            result["store_name"] = parts[0].strip()
            result["branch"] = parts[1].strip()
        elif "-" in line2:
            parts = line2.split("-", 1)
            result["store_name"] = parts[0].strip()
            result["branch"] = parts[1].strip()
    if len(lines) >= 3:
        result["delivery_date"] = lines[2]

    phone_idx = None
    for i, ln in enumerate(lines):
        if is_phone_line(ln):
            phone_idx = i
            break

    start = 3
    end = phone_idx if phone_idx is not None else len(lines)
    for ln in lines[start:end]:
        if "=" in ln:
            prod, qty = ln.split("=", 1)
            result["items"].append({"product": prod.strip(), "qty": qty.strip()})
    if phone_idx is not None:
        result["phone"] = lines[phone_idx]
        note_lines = lines[phone_idx + 1:]
        result["note"] = "\n".join(note_lines).strip() if note_lines else None
    return result


def save_message(event):
    src = event.get("source", {}) or {}
    msg = event.get("message", {}) or {}
    received_at = now_iso()
    text = msg.get("text")
    parsed = parse_order_text(text) if msg.get("type") == "text" else {}
    row = {
        "received_at": received_at,
        "user_id": src.get("userId"),
        "group_id": src.get("groupId"),
        "room_id": src.get("roomId"),
        "event_type": event.get("type"),
        "message_type": msg.get("type"),
        "text": text,
        "raw_json": json.dumps(event, ensure_ascii=False),
        "store_code": parsed.get("store_code"),
        "store_name": parsed.get("store_name"),
        "branch": parsed.get("branch"),
        "delivery_date": parsed.get("delivery_date"),
        "items": parsed.get("items", []),
        "phone": parsed.get("phone"),
        "note": parsed.get("note"),
    }
    sb = get_supabase()
    if sb is not None:
        try:
            payload = dict(row)
            try:
                payload["raw_json"] = json.loads(row["raw_json"]) if row.get("raw_json") else None
            except Exception:
                payload["raw_json"] = row.get("raw_json")
            # items as list (jsonb) for supabase
            payload["items"] = row.get("items", [])
            sb.table(SUPABASE_TABLE).insert(payload).execute()
        except Exception as e:
            global _last_db_error
            _last_db_error = f"insert: {e}"
            print("supabase insert error:", e)
            try:
                conn = get_db()
                conn.execute(
                    """INSERT INTO messages
                       (received_at, user_id, group_id, room_id, event_type, message_type, text, raw_json,
                        store_code, store_name, branch, delivery_date, items, phone, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (received_at, row["user_id"], row["group_id"], row["room_id"],
                     row["event_type"], row["message_type"], row["text"], row["raw_json"],
                     row["store_code"], row["store_name"], row["branch"], row["delivery_date"],
                     json.dumps(row["items"], ensure_ascii=False), row["phone"], row["note"]),
                )
                conn.commit()
                conn.close()
                _last_db_error = f"supabase failed, saved to sqlite: {e}"
            except Exception as e2:
                _last_db_error = f"both failed: {e} | {e2}"
                print("sqlite fallback error:", e2)
    else:
        conn = get_db()
        conn.execute(
            """INSERT INTO messages
               (received_at, user_id, group_id, room_id, event_type, message_type, text, raw_json,
                store_code, store_name, branch, delivery_date, items, phone, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (received_at, row["user_id"], row["group_id"], row["room_id"],
             row["event_type"], row["message_type"], row["text"], row["raw_json"],
             row["store_code"], row["store_name"], row["branch"], row["delivery_date"],
             json.dumps(row["items"], ensure_ascii=False), row["phone"], row["note"]),
        )
        conn.commit()
        conn.close()

    uid = src.get("userId")
    if uid:
        upsert_user(uid)


def upsert_user(user_id):
    profile = get_line_profile(user_id)
    display_name = profile.get("displayName")
    picture_url = profile.get("pictureUrl")
    status_message = profile.get("statusMessage")
    updated_at = now_iso()
    sb = get_supabase()
    if sb is not None:
        try:
            sb.table(SUPABASE_USERS_TABLE).upsert({
                "user_id": user_id,
                "display_name": display_name,
                "picture_url": picture_url,
                "status_message": status_message,
                "updated_at": updated_at,
            }).execute()
        except Exception as e:
            global _last_db_error
            _last_db_error = f"upsert user: {e}"
            print("supabase upsert user error:", e)
    else:
        conn = get_db()
        conn.execute(
            """INSERT INTO users (user_id, display_name, picture_url, status_message, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 display_name=excluded.display_name,
                 picture_url=excluded.picture_url,
                 status_message=excluded.status_message,
                 updated_at=excluded.updated_at""",
            (user_id, display_name, picture_url, status_message, updated_at),
        )
        conn.commit()
        conn.close()


def get_user(user_id):
    sb = get_supabase()
    if sb is not None:
        try:
            r = sb.table(SUPABASE_USERS_TABLE).select("*").eq("user_id", user_id).limit(1).execute()
            data = r.data or []
            return data[0] if data else None
        except Exception as e:
            print("supabase get user error:", e)
            return None
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users(limit=100):
    sb = get_supabase()
    if sb is not None:
        try:
            r = sb.table(SUPABASE_USERS_TABLE).select("*").order("updated_at", desc=True).limit(limit).execute()
            return r.data or []
        except Exception as e:
            print("supabase list users error:", e)
            return []
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_messages(limit=50, offset=0, q="", user_id="", mtype="", date_from="", date_to=""):
    sb = get_supabase()
    if sb is not None:
        try:
            query = sb.table(SUPABASE_TABLE).select("*", count="exact")
            if q:
                query = query.or_(f"text.ilike.%{q}%,user_id.ilike.%{q}%")
            if user_id:
                query = query.eq("user_id", user_id)
            if mtype:
                query = query.eq("message_type", mtype)
            if date_from:
                query = query.gte("received_at", date_from)
            if date_to:
                query = query.lte("received_at", date_to)
            query = query.order("id", desc=True).range(offset, offset + limit - 1)
            r = query.execute()
            rows = r.data or []
            total = r.count if r.count is not None else len(rows)
            return rows, total
        except Exception as e:
            print("supabase list error:", e)
            return [], 0
    conn = get_db()
    where = []
    params = []
    if q:
        where.append("(text LIKE ? OR user_id LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if mtype:
        where.append("message_type = ?")
        params.append(mtype)
    if date_from:
        where.append("received_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("received_at <= ?")
        params.append(date_to)
    sql = "SELECT * FROM messages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(sql, params).fetchall()
    csql = "SELECT COUNT(*) FROM messages"
    cparams = []
    if where:
        csql += " WHERE " + " AND ".join(where)
        cparams = params[:-2]
    total = conn.execute(csql, cparams).fetchone()[0]
    conn.close()
    return [dict(r) for r in rows], total


def all_messages_for_export(limit=100000):
    rows, _ = list_messages(limit=limit, offset=0)
    return rows


def user_history(user_id, limit=100):
    rows, _ = list_messages(limit=limit, offset=0, user_id=user_id)
    return rows


def stats():
    rows, total = list_messages(limit=5000, offset=0)
    today = datetime.now(timezone.utc).date().isoformat()
    today_count = sum(1 for r in rows if str(r.get("received_at", "")).startswith(today))
    by_day = defaultdict(int)
    for r in rows:
        d = str(r.get("received_at", ""))[:10]
        if d:
            by_day[d] += 1
    last7 = []
    for i in range(6, -1, -1):
        d = (datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat()
        last7.append({"date": d, "count": by_day.get(d, 0)})
    uc = Counter(r.get("user_id") for r in rows if r.get("user_id"))
    top_users = []
    for uid, c in uc.most_common(5):
        u = get_user(uid)
        name = (u or {}).get("display_name") or uid
        top_users.append({"user_id": uid, "name": name, "count": c})
    tc = Counter(r.get("message_type") or r.get("event_type") for r in rows)
    by_type = [{"type": k, "count": v} for k, v in tc.most_common()]
    users = list_users(1000)
    return {
        "total_messages": total,
        "today_messages": today_count,
        "total_users": len(users),
        "last7": last7,
        "top_users": top_users,
        "by_type": by_type,
    }


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
            src = event.get("source", {}) or {}
            msg = event.get("message", {}) or {}
            append_order_to_sheet([
                now_iso(),
                src.get("userId"),
                src.get("groupId"),
                src.get("roomId"),
                msg.get("type"),
                msg.get("text"),
            ])

            reply_token = event.get("replyToken")
            mtype = msg.get("type")
            user_id = src.get("userId")
            text = msg.get("text", "")

            if mtype == "text":
                print(f"[MSG] user={user_id} text={text!r}")
                reply_message(reply_token, [
                    {"type": "text", "text": f"บันทึกข้อความแล้ว ✅\n{text}"}
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

        elif event.get("type") == "join":
            reply_token = event.get("replyToken")
            reply_message(reply_token, [
                {"type": "text", "text": "เข้ากลุ่มแล้วครับ ส่งข้อความมาได้เลย"}
            ])

    return "OK", 200


# ---------- Read messages ----------
@app.route("/messages", methods=["GET"])
def messages():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    q = request.args.get("q", "").strip()
    user_id = request.args.get("user_id", "").strip()
    mtype = request.args.get("type", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()

    rows, total = list_messages(limit=limit, offset=offset, q=q, user_id=user_id,
                               mtype=mtype, date_from=date_from, date_to=date_to)

    if request.args.get("format") == "json":
        return jsonify({
            "total": total,
            "limit": limit,
            "offset": offset,
            "messages": rows,
        })

    html = """
    <!doctype html><html><head><meta charset="utf-8"><title>LINE Messages</title>
    <style>body{font-family:sans-serif;margin:20px}table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:8px;font-size:13px}th{background:#06c755;color:#fff}
    tr:nth-child(even){background:#f9f9f9}input,select{padding:6px;margin:4px}
    .bar{margin-bottom:12px}</style></head><body>
    <h2>📩 ข้อความที่บอทได้รับ ({{total}} รายการ)</h2>
    <div class="bar">
    <form method="get">
      <input name="q" placeholder="ค้นหา..." value="{{q}}">
      <input name="user_id" placeholder="user_id" value="{{user_id}}">
      <input name="type" placeholder="type" value="{{mtype}}">
      <input name="from" placeholder="from ISO" value="{{date_from}}">
      <input name="to" placeholder="to ISO" value="{{date_to}}">
      <input name="limit" type="number" value="{{limit}}" style="width:70px">
      <button>ค้นหา</button>
      <a href="/messages/export.csv">Export CSV</a> |
      <a href="/dashboard">Dashboard</a> |
      <a href="/users">Users</a>
    </form></div>
    <table><tr><th>ID</th><th>เวลา</th><th>User</th><th>ร้าน</th><th>ชื่อ-สาขา</th><th>วันส่ง</th><th>รายการ</th><th>โทร</th><th>หมายเหตุ</th></tr>
    {% for r in rows %}
    <tr><td>{{r['id']}}</td><td>{{r['received_at']}}</td><td>{{r['user_id']}}</td>
    <td>{{r['store_code']}}</td><td>{{r['store_name']}}{% if r['branch'] %} - {{r['branch']}}{% endif %}</td>
    <td>{{r['delivery_date']}}</td><td>{{r['items']}}</td><td>{{r['phone']}}</td><td>{{r['note']}}</td></tr>
    {% endfor %}
    </table></body></html>
    """
    return render_template_string(html, rows=rows, total=total, q=q, user_id=user_id,
                                 mtype=mtype, date_from=date_from, date_to=date_to, limit=limit)


@app.route("/messages/export.csv", methods=["GET"])
def export_csv():
    rows, _ = list_messages(limit=1000000, offset=0)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "received_at", "user_id", "store_code", "store_name", "branch",
                     "delivery_date", "items", "phone", "note", "text"])
    for r in rows:
        items = r.get("items")
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                pass
        if isinstance(items, list):
            items_str = "; ".join(f"{it.get('product')}={it.get('qty')}" for it in items)
        else:
            items_str = items
        writer.writerow([r.get("id"), r.get("received_at"), r.get("user_id"), r.get("store_code"),
                        r.get("store_name"), r.get("branch"), r.get("delivery_date"), items_str,
                        r.get("phone"), r.get("note"), r.get("text")])
    resp = Response(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=messages.csv"
    return resp


@app.route("/stats", methods=["GET"])
def stats_endpoint():
    return jsonify(stats())


@app.route("/dashboard", methods=["GET"])
def dashboard():
    html = """
    <!doctype html><html><head><meta charset="utf-8"><title>LINE Bot Data</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
    body{font-family:sans-serif;margin:0;background:#f4f6f8}
    header{background:#06c755;color:#fff;padding:16px 20px}
    .wrap{max-width:1100px;margin:0 auto;padding:20px}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}
    .card{background:#fff;padding:16px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
    .card h3{margin:0;font-size:13px;color:#666}.card .v{font-size:26px;font-weight:bold;margin-top:4px}
    .chart{background:#fff;padding:16px;border-radius:8px;margin:20px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
    .chart canvas{max-height:260px}
    .panel{background:#fff;padding:16px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin:20px 0}
    table{border-collapse:collapse;width:100%}th,td{border:1px solid #eee;padding:8px;font-size:13px}
    th{background:#06c755;color:#fff}tr:nth-child(even){background:#fafafa}
    input,select{padding:6px;margin:4px;border:1px solid #ddd;border-radius:4px}
    button{padding:6px 12px;background:#06c755;color:#fff;border:none;border-radius:4px;cursor:pointer}
    .topbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
    .pager{display:flex;gap:8px;align-items:center;margin-top:10px}
    </style></head><body>
    <header><h1 style="margin:0">📩 LINE Bot — ข้อมูลที่เก็บมา</h1></header>
    <div class="wrap">
      <div class="grid">
        <div class="card"><h3>ข้อความทั้งหมด</h3><div class="v" id="total">-</div></div>
        <div class="card"><h3>วันนี้</h3><div class="v" id="today">-</div></div>
        <div class="card"><h3>ผู้ใช้ทั้งหมด</h3><div class="v" id="users">-</div></div>
      </div>
      <div class="chart"><canvas id="dayChart"></canvas></div>
      <div class="chart"><canvas id="typeChart"></canvas></div>
      <div class="panel">
        <h3 style="margin-top:0">ข้อความ</h3>
        <div class="topbar">
          <input id="q" placeholder="ค้นหา...">
          <input id="uid" placeholder="user_id">
          <button onclick="loadMsgs(0)">ค้นหา</button>
          <a href="/messages/export.csv"><button type="button">Export CSV</button></a>
        </div>
        <table><thead><tr><th>ID</th><th>เวลา</th><th>User</th><th>ร้าน</th><th>ชื่อ-สาขา</th><th>วันส่ง</th><th>รายการ</th><th>โทร</th><th>หมายเหตุ</th></tr></thead>
        <tbody id="tbody"></tbody></table>
        <div class="pager">
          <button onclick="prevPage()">◀ ก่อนหน้า</button>
          <span id="pageinfo"></span>
          <button onclick="nextPage()">ถัดไป ▶</button>
        </div>
      </div>
      <div class="panel">
        <h3 style="margin-top:0">Top users</h3><ul id="top"></ul>
        <p><a href="/users">ดูทั้งหมด</a> | <a href="/messages">หน้า messages แบบเดิม</a></p>
      </div>
    </div>
    <script>
    let offset=0, limit=20, total=0;
    async function loadStats(){
      const d=await (await fetch('/stats')).json();
      document.getElementById('total').textContent=d.total_messages;
      document.getElementById('today').textContent=d.today_messages;
      document.getElementById('users').textContent=d.total_users;
      new Chart(document.getElementById('dayChart'),{type:'bar',data:{labels:d.last7.map(x=>x.date),datasets:[{label:'ข้อความ',data:d.last7.map(x=>x.count),backgroundColor:'#06c755'}]},options:{plugins:{title:{display:true,text:'ข้อความ 7 วันล่าสุด'}}}});
      new Chart(document.getElementById('typeChart'),{type:'doughnut',data:{labels:d.by_type.map(x=>x.type),datasets:[{data:d.by_type.map(x=>x.count)}]},options:{plugins:{title:{display:true,text:'ตามประเภท'}}}});
      const ul=document.getElementById('top'); ul.innerHTML=''; d.top_users.forEach(u=>{const li=document.createElement('li');li.innerHTML='<a href="/users/'+u.user_id+'/history">'+u.name+'</a> ('+u.count+')';ul.appendChild(li);});
    }
    async function loadMsgs(newOffset){
      if(newOffset!==undefined) offset=newOffset;
      const q=encodeURIComponent(document.getElementById('q').value);
      const uid=encodeURIComponent(document.getElementById('uid').value);
      const url='/messages?format=json&limit='+limit+'&offset='+offset+'&q='+q+'&user_id='+uid+'&type=text';
      const d=await (await fetch(url)).json();
      total=d.total;
      const tb=document.getElementById('tbody'); tb.innerHTML='';
      d.messages.forEach(r=>{
        const tr=document.createElement('tr');
        let items=r.items; if(typeof items==='string'){try{items=JSON.parse(items);}catch(e){}} 
        const itemStr=Array.isArray(items)?items.map(it=>(it.product||'')+'='+ (it.qty||'')).join(', '):(items||'');
        tr.innerHTML='<td>'+r.id+'</td><td>'+r.received_at+'</td><td>'+(r.user_id||'')+'</td><td>'+(r.store_code||'')+'</td><td>'+(r.store_name||'')+(r.branch?' - '+r.branch:'')+'</td><td>'+(r.delivery_date||'')+'</td><td>'+itemStr+'</td><td>'+(r.phone||'')+'</td><td>'+(r.note||'')+'</td>';
        tb.appendChild(tr);
      });
      document.getElementById('pageinfo').textContent='แสดง '+(offset+1)+'-'+Math.min(offset+limit,total)+' จาก '+total;
    }
    function prevPage(){ if(offset>=limit){ loadMsgs(offset-limit);} }
    function nextPage(){ if(offset+limit<total){ loadMsgs(offset+limit);} }
    loadStats(); loadMsgs(0);
    </script>
    </body></html>
    """
    return render_template_string(html)


@app.route("/users", methods=["GET"])
def users_page():
    users = list_users(200)
    html = """
    <!doctype html><html><head><meta charset="utf-8"><title>Users</title>
    <style>body{font-family:sans-serif;margin:20px}table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:8px;font-size:13px}th{background:#06c755;color:#fff}
    img{width:40px;height:40px;border-radius:50%}</style></head><body>
    <h2>👥 ผู้ใช้ ({{n}})</h2>
    <table><tr><th>รูป</th><th>User ID</th><th>ชื่อ</th><th>อัปเดต</th></tr>
    {% for u in users %}
    <tr><td>{% if u['picture_url'] %}<img src="{{u['picture_url']}}">{% endif %}</td>
    <td><a href="/users/{{u['user_id']}}/history">{{u['user_id']}}</a></td>
    <td>{{u['display_name']}}</td><td>{{u['updated_at']}}</td></tr>
    {% endfor %}
    </table>
    <p><a href="/dashboard">Dashboard</a></p>
    </body></html>
    """
    return render_template_string(html, users=users, n=len(users))


@app.route("/users/<user_id>/history", methods=["GET"])
def user_history_page(user_id):
    rows = user_history(user_id, limit=200)
    u = get_user(user_id)
    name = (u or {}).get("display_name") or user_id
    html = """
    <!doctype html><html><head><meta charset="utf-8"><title>History</title>
    <style>body{font-family:sans-serif;margin:20px}table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:8px;font-size:13px}th{background:#06c755;color:#fff}</style></head><body>
    <h2>💬 ประวัติ: {{name}}</h2>
    <table><tr><th>เวลา</th><th>ประเภท</th><th>ข้อความ</th></tr>
    {% for r in rows %}
    <tr><td>{{r['received_at']}}</td><td>{{r['message_type'] or r['event_type']}}</td><td>{{r['text']}}</td></tr>
    {% endfor %}
    </table>
    <p><a href="/users">Users</a> | <a href="/dashboard">Dashboard</a></p>
    </body></html>
    """
    return render_template_string(html, rows=rows, name=name)


@app.route("/debug/insert", methods=["GET"])
def debug_insert():
    global _last_db_error
    sb = get_supabase()
    if sb is None:
        return jsonify({"ok": False, "error": "supabase not configured", "last_db_error": _last_db_error}), 400
    try:
        sb.table(SUPABASE_TABLE).insert({
            "received_at": now_iso(),
            "user_id": "DEBUG",
            "event_type": "debug",
            "message_type": "debug",
            "text": "debug insert test",
            "raw_json": {"debug": True},
        }).execute()
        return jsonify({"ok": True, "last_db_error": _last_db_error})
    except Exception as e:
        _last_db_error = f"debug insert: {e}"
        return jsonify({"ok": False, "error": str(e)}), 500
def health():
    sb = get_supabase()
    info = {
        "status": "ok",
        "time": now_iso(),
        "storage": "supabase" if use_supabase() else "sqlite",
        "supabase_configured": use_supabase(),
        "supabase_client_ready": sb is not None,
        "last_db_error": _last_db_error,
    }
    if sb is not None:
        try:
            r = sb.table(SUPABASE_TABLE).select("id", count="exact").limit(1).execute()
            info["supabase_row_count"] = r.count
        except Exception as e:
            info["supabase_select_error"] = str(e)
    return jsonify(info)


@app.route("/", methods=["GET"])
def index():
    return render_template_string('<meta http-equiv="refresh" content="0; url=/dashboard">กำลังไปหน้า dashboard...')


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
