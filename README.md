# LINE Bot Reader

บอท LINE (Messaging API Webhook) สำหรับ **อ่านข้อความ** ที่ผู้ใช้ส่งมา พร้อมบันทึกลง SQLite และมีหน้าดูข้อความ

## ตั้งค่า
1. สร้าง Channel ใน LINE Developers → Messaging API
2. ตั้งค่า Webhook URL: `https://<host>/webhook`
3. คัดลอก `Channel secret` และ `Channel access token (long-lived)`
4. ตั้ง Environment Variables:
   - `LINE_CHANNEL_SECRET`
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `DB_PATH` (ไม่บังคับ, default `line_messages.db`)
5. ติดตั้ง dependencies: `pip install -r requirements.txt`
6. รัน: `python line_bot_webhook.py` (หรือ `gunicorn line_bot_webhook:app`)

## Endpoints
- `POST /webhook` — รับ event จาก LINE (ตรวจสอบลายเซ็น)
- `GET /messages` — ดูข้อความที่บันทึก (HTML) หรือ `?format=json`
- `GET /health` — ตรวจสอบสถานะ

## Google Sheets (รับออเดอร์)
1. สร้าง Google Sheet, คัดลอก Sheet ID จาก URL
2. Google Cloud Console → สร้าง Service Account → ดาวน์โหลด JSON key → บันทึกเป็น `service_account.json` (หรือใส่ JSON ใน env `GOOGLE_SERVICE_ACCOUNT_JSON`)
3. แชร์ Sheet ให้ service account email (Editor)
4. ตั้ง env: `GOOGLE_SHEET_ID`, `GOOGLE_WORKSHEET` (default Orders), `GOOGLE_SERVICE_ACCOUNT_FILE`/`GOOGLE_SERVICE_ACCOUNT_JSON`
5. ทุกข้อความที่เข้ามาจะถูก append เป็นแถว (received_at, user_id, group_id, room_id, message_type, text) และบอทตอบ "บันทึกออเดอร์แล้ว"