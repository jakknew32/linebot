# LINE Bot Reader

บอท LINE (Messaging API Webhook) สำหรับ **อ่านข้อความ** ที่ผู้ใช้ส่งมา พร้อมบันทึกลง Supabase/SQLite และมีหน้าดูข้อความ + Dashboard

## ตั้งค่า
1. สร้าง Channel ใน LINE Developers → Messaging API
2. ตั้งค่า Webhook URL: `https://<host>/webhook`
3. คัดลอก `Channel secret` และ `Channel access token (long-lived)`
4. ตั้ง Environment Variables (ดู `.env.example`)
5. Supabase: สร้าง project, รัน `schema.sql` ใน SQL Editor, คัดลอก URL + anon/service key
6. ติดตั้ง dependencies: `pip install -r requirements.txt`
7. รัน: `python line_bot_webhook.py` (หรือ `gunicorn line_bot_webhook:app`)

## Endpoints
- `POST /webhook` — รับ event จาก LINE (ตรวจสอบลายเซ็น)
- `GET /messages` — ดูข้อความ (HTML) หรือ `?format=json`
- `GET /messages/export.csv` — ดาวน์โหลด CSV
- `GET /stats` — สถิติ (JSON)
- `GET /dashboard` — Dashboard + กราฟ
- `GET /users` — รายการผู้ใช้
- `GET /users/<id>/history` — ประวัติการสนทนาต่อ user
- `GET /health` — ตรวจสอบสถานะ

## Storage
- ถ้าตั้ง `SUPABASE_URL` + `SUPABASE_KEY` จะใช้ Supabase
- ไม่ตั้ง จะ fallback เป็น SQLite (`DB_PATH`)
