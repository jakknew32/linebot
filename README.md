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

## ค้นหาข้อความ
`/messages?q=คำค้น&limit=50&offset=0`