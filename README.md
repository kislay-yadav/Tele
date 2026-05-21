# 📡 AUTO SENDER PRO — Plugin Edition

## 🔧 Update Without Touching main.py
Edit `config.json` → push to GitHub → visit `/reload` or press ⚙️ Reload Config in bot

## 🚀 Render Deploy
1. Upload all files to GitHub
2. Render → New Web Service → Connect repo  
3. Build: `pip install --upgrade pip && pip install -r requirements.txt`
4. Start: `python main.py`
5. Env vars: `OWNER_ID`, `BOT_TOKEN`, `PYTHON_VERSION=3.12.0`

## 🌐 URLs
- `/` — Main landing page
- `/register` — User login (share this!)
- `/{slug}` — Individual user page
- `/reload` — Hot-reload config.json
- `/health` — Keep-alive endpoint

## 🔀 Response Forwarder Setup (Bot)
1. Select user → 🔀 Forwarder
2. Send: `@source_group→@dest_group`
3. Messages from source forwarded to dest
4. Telegram links auto-opened (bot started)!

## ⏱ Custom Timing Per User
Select user → ⏱ Set Timing → send: `30 90 60 120`
(30-90s between groups, 60-120 min cycle)

## ⚙️ config.json Keys
- `min_delay` / `max_delay` — default group delay (seconds)
- `cycle_min_minutes` / `cycle_max_minutes` — default cycle time
- `plugins.response_forwarder` — enable/disable forwarder
- `plugins.auto_link_opener` — auto start bots from links
- `app_name` / `app_tagline` — customize website text
