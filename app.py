import re
import asyncio
import ast
import base64
import csv
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from io import BytesIO
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiohttp import ClientSession, web
from telegram import Update, InputMediaPhoto, InputMediaVideo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import RetryAfter, TimedOut, NetworkError
from openpyxl import load_workbook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("lead-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DB_PATH = os.getenv("DB_PATH", "/data/lead_bot_v2.db")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PORT = int(os.getenv("PORT", "8080"))
TZ = ZoneInfo(os.getenv("TZ", "Asia/Kuala_Lumpur"))
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "21"))
WA_CHECK_URL = os.getenv("WA_CHECK_URL", "").strip()
WA_CHECK_TOKEN = os.getenv("WA_CHECK_TOKEN", "").strip()

# OneDrive Excel -> Antiscam automatic intake.
# The default is the user's current shared workbook; override with EXCEL_SOURCE_URL anytime.
EXCEL_SOURCE_URL = os.getenv(
    "EXCEL_SOURCE_URL",
    "https://1drv.ms/x/c/daf364a6ebb9bb21/IQCiSJlzcxqTS5koaaaYJNoAARA6O2_sroDPqP1pAvha1Ys?e=Rp9hf1",
).strip()
EXCEL_POLL_SECONDS = max(5, int(os.getenv("EXCEL_POLL_SECONDS", "10")))
EXCEL_IMPORT_EXISTING = os.getenv("EXCEL_IMPORT_EXISTING", "0").strip().lower() in ("1", "true", "yes")



# ---- Reliable Telegram sender: serialize + retry burst traffic ----
_send_locks = {}
_last_send_at = {}

async def reliable_send(app, chat_id, text, **kwargs):
    lock = _send_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        for attempt in range(8):
            try:
                now = asyncio.get_running_loop().time()
                last = _last_send_at.get(chat_id, 0.0)
                wait = 1.10 - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)

                result = await app.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                _last_send_at[chat_id] = asyncio.get_running_loop().time()
                return result

            except RetryAfter as e:
                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)) + 0.5)

            except (TimedOut, NetworkError):
                await asyncio.sleep(min(2 ** attempt, 15))

        raise RuntimeError(f"Telegram send failed after retries: chat_id={chat_id}")


def db():
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS assistants(
          user_id INTEGER PRIMARY KEY, name TEXT, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teams(
          chat_id INTEGER PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS leads(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT,
          phone_raw TEXT, phone TEXT NOT NULL, source TEXT, status TEXT NOT NULL,
          duplicate_of INTEGER, wa_status TEXT NOT NULL DEFAULT 'unknown',
          team_chat_id INTEGER, team_name TEXT, submitted_by INTEGER, payload TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
        CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);
        CREATE TABLE IF NOT EXISTS lead_message_map(
          chat_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          lead_id INTEGER NOT NULL,
          phone TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_lead_message_map_phone ON lead_message_map(phone);
        CREATE TABLE IF NOT EXISTS backup_groups(
          chat_id INTEGER PRIMARY KEY,
          title TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backup_records(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          chat_id INTEGER,
          submitted_by INTEGER,
          submitted_name TEXT,
          bank TEXT,
          name TEXT,
          nric TEXT,
          address TEXT,
          acc_no TEXT,
          card_number TEXT,
          exp_date TEXT,
          cvv_code TEXT,
          nama_ibu TEXT,
          pin_code TEXT,
          o9_username TEXT,
          o9_pas TEXT,
          cawangan_opening TEXT,
          self_value TEXT,
          customer TEXT,
          lala_post TEXT,
          sale_person TEXT,
          area TEXT,
          code TEXT,
          business_type TEXT,
          raw_text TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_backup_name ON backup_records(name);
        CREATE INDEX IF NOT EXISTS idx_backup_code ON backup_records(code);
        CREATE TABLE IF NOT EXISTS backup_cleanup_queue(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          delete_after TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_inbox_config(
          id INTEGER PRIMARY KEY CHECK (id=1),
          chat_id INTEGER,
          enabled INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lead_inbox_routes(
          chat_id INTEGER PRIMARY KEY,
          route_key TEXT NOT NULL,
          title TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_team_routes(
          chat_id INTEGER PRIMARY KEY,
          route_key TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS persistent_settings(
          key TEXT PRIMARY KEY,
          value TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS persistent_team_groups(
          chat_id INTEGER PRIMARY KEY,
          bucket TEXT NOT NULL DEFAULT 'A',
          title TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS persistent_rr_state(
          bucket TEXT PRIMARY KEY,
          next_index INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS persistent_staff(
          user_id INTEGER PRIMARY KEY,
          name TEXT,
          role TEXT NOT NULL DEFAULT 'staff',
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backup_album_sets(
          record_id INTEGER PRIMARY KEY,
          backup_chat_id INTEGER NOT NULL,
          message_ids_json TEXT NOT NULL,
          caption_message_id INTEGER,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backup_route(
          id INTEGER PRIMARY KEY CHECK (id=1),
          source_chat_id INTEGER,
          backup_chat_id INTEGER,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backup_message_map(
          source_chat_id INTEGER NOT NULL,
          source_message_id INTEGER NOT NULL,
          backup_chat_id INTEGER NOT NULL,
          backup_message_id INTEGER NOT NULL,
          record_id INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(source_chat_id, source_message_id)
        );
        CREATE TABLE IF NOT EXISTS backup_album_sets(
          record_id INTEGER PRIMARY KEY,
          backup_chat_id INTEGER NOT NULL,
          message_ids_json TEXT NOT NULL,
          caption_message_id INTEGER,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_status(
          phone TEXT PRIMARY KEY, status TEXT NOT NULL, marked_by INTEGER,
          marked_name TEXT, marked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backup_followups(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          record_id INTEGER,
          source_chat_id INTEGER NOT NULL,
          source_message_id INTEGER NOT NULL,
          code TEXT,
          sale_person TEXT,
          responsible_user_id INTEGER,
          responsible_name TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          next_reminder_at TEXT,
          last_reminded_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(source_chat_id, source_message_id)
        );
        CREATE TABLE IF NOT EXISTS backup_record_confirmations(
          record_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'pending',
          confirmed_by INTEGER,
          confirmed_name TEXT,
          confirmed_at TEXT,
          snapshot_json TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS submitters(
          user_id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_resend_pending(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          requested_by INTEGER NOT NULL,
          kind TEXT NOT NULL,
          old_lead_id INTEGER,
          data_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO settings(key,value) VALUES('rr_index','0');
        """)
        # Permanent report/audit fields. Safe migration for existing Railway DBs.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(leads)").fetchall()}
        if "source_created_at" not in cols:
            c.execute("ALTER TABLE leads ADD COLUMN source_created_at TEXT")
        if "external_id" not in cols:
            c.execute("ALTER TABLE leads ADD COLUMN external_id TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_leads_source_created ON leads(source_created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_leads_external_id ON leads(external_id)")


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def _clean_phone_digits(value) -> str:
    """Return only phone digits while tolerating common Excel/Telegram formatting.

    Examples handled here: +60..., 0060..., spaces, dashes, brackets and an
    Excel-looking integer rendered as ``60123456789.0``.  This helper does not
    decide whether the number is Malaysian; validation stays in the normalizers.
    """
    raw = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0060"):
        digits = digits[2:]
    # Tolerate the occasional international + trunk form 6001... -> 601...
    if digits.startswith("6001") and len(digits) in (12, 13):
        digits = "60" + digits[3:]
    return digits


def normalize_phone(value: str) -> str:
    """Normalize an explicit Malaysian mobile phone field to +60 format.

    Because ``value`` is already a phone field, a missing Malaysian trunk zero is
    safe to restore: 196025824 -> 0196025824 -> +60196025824.
    """
    digits = _clean_phone_digits(value)
    if digits.startswith("01") and len(digits) in (10, 11):
        digits = "60" + digits[1:]
    elif digits.startswith("1") and len(digits) in (9, 10):
        # Excel frequently strips the leading 0 from a local Malaysian mobile.
        digits = "60" + digits
    if not (digits.startswith("601") and len(digits) in (11, 12)):
        raise ValueError("电话号码格式不正确")
    local = digits[2:]
    if len(set(local)) <= 2 or re.fullmatch(r"(123)+", local):
        raise ValueError("电话号码格式不正确")
    return "+" + digits


def extract_phones(text: str):
    """Extract likely MY phone numbers from normal pasted Lead text."""
    if not text:
        return []
    patterns = [
        r"(?<!\d)(?:\+?60|0060)[\s().-]*1\d(?:[\s().-]*\d){7,8}(?!\d)",
        r"(?<!\d)01\d(?:[\s().-]*\d){7,8}(?!\d)",
    ]
    found = []
    seen = set()
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            raw = m.group(0)
            try:
                normalized = normalize_phone(raw)
            except ValueError:
                continue
            if normalized not in seen:
                seen.add(normalized)
                found.append((raw, normalized))
    return found


def guess_name(text: str) -> str:
    # Common labels from Facebook/SaveMyLeads exports.
    for label in ("name", "full name", "full_name", "nama", "姓名", "名字", "customer"):
        m = re.search(rf"(?im)^\s*{re.escape(label)}\s*[:：=-]\s*(.+?)\s*$", text or "")
        if m:
            value = m.group(1).strip()
            if value:
                return value[:80]
    return "未填写"


def get_setting(key: str, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))


def allowed(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    with db() as c:
        return c.execute("SELECT 1 FROM assistants WHERE user_id=?", (user_id,)).fetchone() is not None


async def require(update: Update, owner=False) -> bool:
    uid = update.effective_user.id
    ok = uid == OWNER_ID if owner else allowed(uid)
    if not ok:
        await update.effective_message.reply_text("❌ 你没有操作权限。")
    return ok


def extract_user_id(update: Update, args):
    if update.effective_message.reply_to_message:
        u = update.effective_message.reply_to_message.from_user
        return u.id, u.full_name
    if args and args[0].lstrip("-").isdigit():
        return int(args[0]), " ".join(args[1:]) or str(args[0])
    return None, None


STATUS_MAP = {
    "中过了": ("hit_before", "✅ 中过了"),
    "中過了": ("hit_before", "✅ 中过了"),
    "used before": ("hit_before", "✅ 中过了"),
    "used": ("hit_before", "✅ 中过了"),

    "警察": ("police", "🚔 警察"),
    "police": ("police", "🚔 警察"),

    "无效": ("no_ws", "❌ 没WS"),
    "無效": ("no_ws", "❌ 没WS"),
    "没ws": ("no_ws", "❌ 没WS"),
    "沒ws": ("no_ws", "❌ 没WS"),
    "no whatsapp": ("no_ws", "❌ 没WS"),
    "no whatsApp": ("no_ws", "❌ 没WS"),
    "no ws": ("no_ws", "❌ 没WS"),

    "成交": ("closed", "💰 成交"),
    "deal": ("closed", "💰 成交"),
    "closed": ("closed", "💰 成交"),
}

def latest_status(phone):
    with db() as c:
        return c.execute("SELECT * FROM lead_status WHERE phone=?", (phone,)).fetchone()

async def status_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return

    raw_text = (msg.text or "").strip()
    text = raw_text.casefold()
    username = (context.bot.username or "").lower()
    if username:
        text = text.replace(f"@{username}", "").strip()

    cancel_words = ("取消状态", "取消狀態", "取消", "cancel", "remove status")
    is_cancel = any(k.casefold() == text for k in cancel_words)

    selected = None
    status_map = STATUS_MAP
    if update.effective_chat and int(update.effective_chat.id) == ANTISCAM_STATUS_CHAT_ID:
        # Explicit aliases only; ordinary chat must not become a status update.
        text = re.sub(r"\s+", "", text)
        status_map = {re.sub(r"\s+", "", k.casefold()): v for k, v in STATUS_MAP.items()}
        for alias in ("没有ws", "沒有ws", "没whatsapp", "沒whatsapp",
                      "没有whatsapp", "沒有whatsapp", "无ws", "無ws"):
            status_map[alias] = ("no_ws", "❌ 没WS")
        is_cancel = any(re.sub(r"\s+", "", k.casefold()) == text for k in cancel_words)
    for keyword, value in status_map.items():
        if keyword.casefold() == text:
            selected = value
            break

    # If it isn't a status keyword, leave normal chat alone.
    if not selected and not is_cancel:
        return

    # Staff typed a valid status but forgot to Reply a Lead.
    if not msg.reply_to_message:
        await msg.reply_text(
            "⚠️ 请先 Reply 客户 Lead，再输入状态。\\n"
            "⚠️ Please reply to the customer's Lead first."
        )
        return

    # Resolve the Lead phone robustly from the replied message or a short reply chain.
    phone = None
    cur = msg.reply_to_message
    for _ in range(4):
        if not cur:
            break
        body = cur.text or cur.caption or ""
        phones = (_s_lead_phones(body) if update.effective_chat
                  and int(update.effective_chat.id) == ANTISCAM_STATUS_CHAT_ID
                  else extract_phones(body))
        if phones:
            phone = phones[0][1]
            break
        cur = getattr(cur, "reply_to_message", None)

    # Fallback for bot-distributed Leads: exact chat + Telegram message id mapping.
    if not phone:
        try:
            with db() as c:
                row = c.execute(
                    "SELECT phone FROM lead_message_map WHERE chat_id=? AND message_id=? LIMIT 1",
                    (int(update.effective_chat.id), int(msg.reply_to_message.message_id)),
                ).fetchone()
            if row:
                phone = row["phone"]
        except Exception:
            LOG.exception("lead status map lookup failed")

    if not phone:
        await msg.reply_text(
            "⚠️ 找不到这份 Lead 的号码，请 Reply Bot 发出的客户 Lead。\n"
            "⚠️ Lead mapping not found. Please reply to the Lead sent by the bot."
        )
        return

    if is_cancel:
        with db() as c:
            existed = c.execute("SELECT 1 FROM lead_status WHERE phone=?", (phone,)).fetchone()
            c.execute("DELETE FROM lead_status WHERE phone=?", (phone,))
        if existed:
            await msg.reply_text("↩️ 状态已取消 / Status cancelled")
        else:
            await msg.reply_text("ℹ️ 目前没有状态 / No status currently")
        return

    status_key, label = selected
    marked_name = user.full_name or user.username or str(user.id)
    with db() as c:
        c.execute(
            """INSERT INTO lead_status(phone,status,marked_by,marked_name,marked_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(phone) DO UPDATE SET
                 status=excluded.status,
                 marked_by=excluded.marked_by,
                 marked_name=excluded.marked_name,
                 marked_at=excluded.marked_at""",
            (phone, status_key, user.id, marked_name, now_iso()),
        )
    await msg.reply_text(f"{label} · 已记录 / Recorded")


# =========================
# Backup Bot merged module
# Locked preservation fix: ONLY the known 888 -> 888 backup route.
BACKUP_PRESERVE_SOURCE_ID = -1003941232666
BACKUP_PRESERVE_TARGET_ID = -1004348567725

def backup_preserve_route_enabled(route):
    try:
        return (
            int(route["source_chat_id"] or 0) == BACKUP_PRESERVE_SOURCE_ID
            and int(route["backup_chat_id"] or 0) == BACKUP_PRESERVE_TARGET_ID
        )
    except Exception:
        return False

# =========================

# Telegram media-group (album) buffer.
# One Telegram album arrives as multiple separate updates, so wait briefly
# and process the whole group together.
BACKUP_ALBUM_BUFFER = {}
BACKUP_ALBUM_TASKS = {}


def backup_get_route():
    with db() as c:
        return c.execute(
            "SELECT source_chat_id, backup_chat_id FROM backup_route WHERE id=1"
        ).fetchone()

def backup_set_route(source_chat_id=None, backup_chat_id=None):
    with db() as c:
        row = c.execute(
            "SELECT source_chat_id, backup_chat_id FROM backup_route WHERE id=1"
        ).fetchone()

        src = source_chat_id if source_chat_id is not None else (
            row["source_chat_id"] if row else None
        )
        dst = backup_chat_id if backup_chat_id is not None else (
            row["backup_chat_id"] if row else None
        )

        c.execute(
            """INSERT INTO backup_route(id,source_chat_id,backup_chat_id,updated_at)
               VALUES(1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 source_chat_id=excluded.source_chat_id,
                 backup_chat_id=excluded.backup_chat_id,
                 updated_at=excluded.updated_at""",
            (src, dst, now_iso()),
        )

def backup_route_matches_source(chat_id):
    route = backup_get_route()
    return bool(
        route
        and route["source_chat_id"]
        and route["backup_chat_id"]
        and int(route["source_chat_id"]) == int(chat_id)
    )

async def backup_copy_single_message(app, route, msg, safe_text):
    """Copy one 888 source message to permanent Backup, preserving media when present."""
    has_media = bool(
        getattr(msg, "photo", None) or getattr(msg, "video", None) or
        getattr(msg, "animation", None) or getattr(msg, "document", None) or
        getattr(msg, "audio", None) or getattr(msg, "voice", None) or
        getattr(msg, "video_note", None) or getattr(msg, "sticker", None)
    )
    if has_media:
        kwargs = dict(
            chat_id=route["backup_chat_id"],
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )
        # Telegram captions are limited to 1024 chars.  Do not force an empty
        # caption because that can strip the original caption on copy.
        if safe_text:
            kwargs["caption"] = safe_text[:1024]
        copied = await app.bot.copy_message(**kwargs)
        return copied.message_id

    sent = await app.bot.send_message(
        chat_id=route["backup_chat_id"],
        text=safe_text[:4096],
    )
    return sent.message_id


def backup_parse_5_index(text):
    """Parse searchable Backup indexes without altering original caption."""
    text = text or ""

    def pick(patterns):
        for pat in patterns:
            m = re.search(pat, text, flags=re.I | re.M)
            if m:
                return (m.group(1) or "").strip()
        return ""

    return {
        "name": pick([
            r"^\s*(?:📌\s*)?NAME\s*:\s*(.+?)\s*$",
            r"^\s*Nama\s*Ibu\s*:\s*(.+?)\s*$",
        ]),
        "nric": pick([
            r"^\s*(?:📌\s*)?NRIC\s*:\s*([0-9\-]+)\s*$",
            r"^\s*PIN\s*:\s*([0-9\-]+)\s*$",
        ]),
        "self_phone": pick([
            r"^\s*Self\s*:\s*([+0-9][0-9\-\s]+)\s*$",
        ]),
        "customer": pick([
            r"^\s*Customer\s*:\s*([+0-9][0-9\-\s]+)\s*$",
        ]),
        "phone": pick([
            r"^\s*Customer\s*:\s*([+0-9][0-9\-\s]+)\s*$",
            r"^\s*Self\s*:\s*([+0-9][0-9\-\s]+)\s*$",
        ]),
        "sales": pick([
            r"^\s*Sale\s*person\s*:\s*(.+?)\s*$",
        ]),
        "sale_person": pick([
            r"^\s*Sale\s*person\s*:\s*(.+?)\s*$",
        ]),
        "code": pick([
            r"^\s*Code\s*:\s*(.+?)\s*$",
        ]),
        "full_text": text,
    }


def backup_redact_full_text(text):
    """Compatibility helper for Backup pipeline.
    Backup is an archive copy: preserve the supplied caption/text verbatim.
    """
    return text or ""


def backup_full_details_message(title, full_text, footer=""):
    """Show the complete supplied 888 record below the bot status title."""
    details = (full_text or "").strip() or "-"
    prefix = f"{title}\n\n📋 完整资料：\n"
    suffix = f"\n\n{footer}" if footer else ""
    # Telegram text limit is 4096. Preserve the title/footer and use all
    # remaining room for the original record instead of a short field summary.
    room = max(0, 4096 - len(prefix) - len(suffix))
    if len(details) > room:
        details = details[:max(0, room - 5)] + "\n…"
    return prefix + details + suffix


def _backup_table_columns(conn, table):
    try:
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []

def _backup_find_table(conn):
    for t in ("backup_5_index","backup_index","backup_records","backup_search_index","backup_items"):
        if _backup_table_columns(conn,t):
            return t,_backup_table_columns(conn,t)
    return None,[]

def backup_save_5_index(data, source_chat_id, operator_id, operator_name,
                        source_message_id, backup_chat_id, backup_message_id):
    """Create/update one canonical Backup record. Code first, NRIC fallback."""
    data = data or {}
    code = (data.get("code") or "").strip()
    nric = (data.get("nric") or "").strip()
    with db() as c:
        # Use the application's canonical backup_records table.
        old = None
        if code:
            old = c.execute(
                "SELECT * FROM backup_records WHERE UPPER(TRIM(code))=UPPER(?) ORDER BY id DESC LIMIT 1",
                (code,)
            ).fetchone()
        if old is None and nric:
            old = c.execute(
                "SELECT * FROM backup_records WHERE REPLACE(REPLACE(nric,'-',''),' ','')=? ORDER BY id DESC LIMIT 1",
                (nric.replace("-","").replace(" ",""),)
            ).fetchone()

        vals = {
            "name": data.get("name",""), "nric": nric,
            "customer": data.get("customer",""),
            "self_value": data.get("self_phone", ""),
            "sale_person": data.get("sale_person",""),
            "raw_text": data.get("full_text", ""),
            "code": code, "source_chat_id": source_chat_id,
            "source_message_id": source_message_id, "backup_chat_id": backup_chat_id,
            "backup_message_id": backup_message_id, "operator_id": operator_id,
            "operator_name": operator_name, "updated_at": now_iso()
        }
        cols=[r["name"] for r in c.execute("PRAGMA table_info(backup_records)").fetchall()]
        vals={k:v for k,v in vals.items() if k in cols}

        if old:
            sets=",".join(f"{k}=?" for k in vals)
            c.execute(f"UPDATE backup_records SET {sets} WHERE id=?",
                      tuple(vals.values())+(old["id"],))
            row=c.execute("SELECT * FROM backup_records WHERE id=?",(old["id"],)).fetchone()
            return "updated", row

        if "created_at" in cols:
            vals["created_at"]=now_iso()
        keys=list(vals)
        c.execute(f"INSERT INTO backup_records ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                  tuple(vals[k] for k in keys))
        rid=c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        row=c.execute("SELECT * FROM backup_records WHERE id=?",(rid,)).fetchone()
        return "created", row

def backup_search(query, limit=20):
    """Robust /find lookup against the canonical Backup index.

    Convenience goals:
      * Code lookup ignores case, spaces, '-' and '/': M4674 == M 4674 == M/4674.
      * Search NAME / NRIC / Customer / Self / Sale person / stored raw text too.
      * Keep the canonical backup_records id so /find can copy the full saved album.
    """
    q = (query or "").strip()
    if not q:
        return []

    code_key = re.sub(r"[^A-Za-z0-9]", "", q).upper()
    like = f"%{q}%"

    with db() as c:
        cols = _backup_table_columns(c, "backup_records")
        if not cols:
            return []

        searchable = [
            x for x in (
                "code", "name", "nric", "customer", "self_value",
                "sale_person", "raw_text", "address", "acc_no",
                "card_number", "bank", "area"
            ) if x in cols
        ]

        clauses = []
        params = []
        for col in searchable:
            clauses.append(f"CAST({col} AS TEXT) LIKE ? COLLATE NOCASE")
            params.append(like)

        # Code is the most common lookup.  Normalize separators so staff can type
        # M4674 / m4674 / M 4674 / M-4674 / M/4674 interchangeably.
        if "code" in cols and code_key:
            clauses.append(
                "UPPER(REPLACE(REPLACE(REPLACE(REPLACE(CAST(code AS TEXT),' ',''),'-',''),'/',''),'\\','')) = ?"
            )
            params.append(code_key)

        if not clauses:
            return []

        order_col = "id" if "id" in cols else "rowid"
        rows = c.execute(
            f"SELECT * FROM backup_records WHERE ({' OR '.join(clauses)}) "
            f"ORDER BY {order_col} DESC LIMIT ?",
            tuple(params) + (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]



def backup_message_map_media_columns():
    """Ensure per-photo fingerprints exist for exact 888 -> Backup mirroring."""
    try:
        with db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(backup_message_map)").fetchall()}
            if "source_file_unique_id" not in cols:
                c.execute("ALTER TABLE backup_message_map ADD COLUMN source_file_unique_id TEXT")
            if "source_file_id" not in cols:
                c.execute("ALTER TABLE backup_message_map ADD COLUMN source_file_id TEXT")
    except Exception:
        LOG.exception("backup message media-column migration failed")

def backup_lookup_map(source_chat_id, source_message_id=None):
    """Resolve one 888 source message to its exact Backup copy.

    The old compatibility helper accepted a free-text query, but the edit
    pipeline calls this with (source_chat_id, source_message_id).  Returning
    the DB mapping here is critical: otherwise edited captions never reach
    the matching Backup album item.
    """
    if source_message_id is None:
        # Keep legacy one-argument behavior for any older code paths.
        query = source_chat_id
        return {str(r.get("media_group_id") or r.get("message_id") or r.get("id")): r
                for r in backup_search(query, 50)
                if (r.get("media_group_id") or r.get("message_id") or r.get("id")) is not None}

    try:
        backup_message_map_media_columns()
        with db() as c:
            row = c.execute(
                """SELECT *
                   FROM backup_message_map
                   WHERE source_chat_id=? AND source_message_id=?
                   LIMIT 1""",
                (int(source_chat_id), int(source_message_id)),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        LOG.exception("backup message map lookup failed")
        return None

def backup_queue_delete(*args, **kwargs):
    return None


def backup_queue_receipt_delete(chat_id, message_id, seconds=60):
    """Delete only temporary Bot status receipts; never queue source/Backup media."""
    if not chat_id or not message_id:
        return
    delete_after = (datetime.now(TZ) + timedelta(seconds=int(seconds))).isoformat()
    with db() as c:
        c.execute(
            "INSERT INTO backup_cleanup_queue(chat_id,message_id,delete_after) VALUES(?,?,?)",
            (int(chat_id), int(message_id), delete_after),
        )

def backup_largest_contiguous_message_set(message_ids):
    ids=sorted(set(int(x) for x in (message_ids or [])))
    if not ids:return []
    best=[]; cur=[]
    for x in ids:
        if cur and x!=cur[-1]+1:
            if len(cur)>len(best):best=cur[:]
            cur=[]
        cur.append(x)
    return cur if len(cur)>len(best) else best

async def backup_flush_album(app, key):
    # Telegram may deliver one user-sent album as several updates with small gaps.
    # The task is reset on every album item; a 4s quiet window ensures all photos
    # are collected before the canonical Backup album is created.
    await asyncio.sleep(4.0)

    item = BACKUP_ALBUM_BUFFER.pop(key, None)
    BACKUP_ALBUM_TASKS.pop(key, None)
    if not item:
        return

    chat_id = item["chat_id"]
    route = backup_get_route()
    if not route or int(route["source_chat_id"] or 0) != int(chat_id) or not route["backup_chat_id"]:
        return

    messages = sorted(item["messages"], key=lambda x: x.message_id)
    if await backup_copy_reply_photos(app, messages, chat_id):
        return
    caption_msg = next((m for m in messages if (m.caption or "").strip()), None)
    text = (caption_msg.caption if caption_msg else "") or ""
    data = backup_parse_5_index(text)
    if not data:
        return

    safe_text = backup_redact_full_text(text)

    media = []
    media_sources = []
    for m in messages:
        cap = safe_text[:1024] if caption_msg and m.message_id == caption_msg.message_id else None
        if m.photo:
            media_sources.append(m)
            media.append(InputMediaPhoto(media=m.photo[-1].file_id, caption=cap))
        elif getattr(m, "video", None):
            media_sources.append(m)
            media.append(InputMediaVideo(media=m.video.file_id, caption=cap))

    if not media:
        # Unsupported media-group type: copy every item individually rather
        # than silently dropping it from the 888 -> backup route.
        for m in messages:
            try:
                await app.bot.copy_message(
                    chat_id=route["backup_chat_id"],
                    from_chat_id=chat_id,
                    message_id=m.message_id,
                )
            except Exception:
                LOG.exception("media-group item copy to backup failed")
        return

    # Capture previous canonical Backup album BEFORE replacing the record.
    old_record = None
    old_backup_ids = []
    code_key = (data.get("code") or "").strip()
    nric_key = (data.get("nric") or "").strip()
    with db() as c:
        if code_key:
            old_record = c.execute(
                "SELECT * FROM backup_records WHERE UPPER(TRIM(code))=UPPER(?) ORDER BY id DESC LIMIT 1",
                (code_key,)
            ).fetchone()
        if old_record is None and nric_key:
            old_record = c.execute(
                "SELECT * FROM backup_records WHERE REPLACE(REPLACE(nric,'-',''),' ','')=? ORDER BY id DESC LIMIT 1",
                (nric_key.replace("-","").replace(" ",""),)
            ).fetchone()
        if old_record:
            try:
                rr = c.execute(
                    "SELECT backup_message_id FROM backup_message_map WHERE record_id=? ORDER BY backup_message_id",
                    (old_record["id"],)
                ).fetchall()
                old_backup_ids = [int(r["backup_message_id"]) for r in rr if r["backup_message_id"]]
            except Exception:
                old_backup_ids = []
            if not old_backup_ids and old_record["backup_message_id"]:
                old_backup_ids = [int(old_record["backup_message_id"])]

    try:
        sent_group = await app.bot.send_media_group(
            chat_id=route["backup_chat_id"],
            media=media,
        )
    except Exception:
        LOG.exception("grouped album send to backup failed")
        return

    source_id = caption_msg.message_id if caption_msg else media_sources[0].message_id
    user = item["user"]

    action, row = backup_save_5_index(
        data, chat_id, user.id,
        user.full_name or user.username or str(user.id),
        source_id, route["backup_chat_id"], sent_group[0].message_id
    )

    with db() as c:
        backup_message_map_media_columns()
        for src_msg, dst_msg in zip(photo_sources, sent_group):
            src_photo = src_msg.photo[-1] if src_msg.photo else None
            c.execute(
                """INSERT INTO backup_message_map(
                   source_chat_id,source_message_id,backup_chat_id,backup_message_id,
                   record_id,created_at,updated_at,source_file_unique_id,source_file_id
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET
                   backup_chat_id=excluded.backup_chat_id,
                   backup_message_id=excluded.backup_message_id,
                   record_id=excluded.record_id,
                   source_file_unique_id=excluded.source_file_unique_id,
                   source_file_id=excluded.source_file_id,
                   updated_at=excluded.updated_at""",
                (
                    chat_id, src_msg.message_id, route["backup_chat_id"], dst_msg.message_id,
                    row["id"], now_iso(), now_iso(),
                    (src_photo.file_unique_id if src_photo else None),
                    (src_photo.file_id if src_photo else None),
                ),
            )

    # Canonical Backup media handling.
    caption_backup_id = None
    for src_msg, dst_msg in zip(photo_sources, sent_group):
        if caption_msg and src_msg.message_id == caption_msg.message_id:
            caption_backup_id = dst_msg.message_id
            break
    if caption_backup_id is None and sent_group:
        caption_backup_id = sent_group[0].message_id

    new_ids = [int(m.message_id) for m in sent_group]

    if old_record and backup_preserve_route_enabled(route):
        # 888 locked route: the newly completed upload becomes the canonical Backup.
        # Send the complete new album first; only after success delete the old Backup
        # messages. This prevents the old record disappearing before the replacement exists.
        backup_set_canonical_album(
            row["id"], route["backup_chat_id"], new_ids, caption_backup_id
        )
        new_id_set = set(new_ids)
        for old_mid in old_backup_ids:
            if int(old_mid) in new_id_set:
                continue
            try:
                await app.bot.delete_message(chat_id=route["backup_chat_id"], message_id=int(old_mid))
            except Exception:
                LOG.warning("Could not delete replaced 888 backup message %s", old_mid, exc_info=True)
        if old_record and new_id_set:
            with db() as c:
                c.execute(
                    "DELETE FROM backup_message_map WHERE record_id=? AND backup_message_id NOT IN (%s)"
                    % ",".join("?" for _ in new_id_set),
                    (old_record["id"], *tuple(new_id_set))
                )
    else:
        # Preserve legacy behavior for every other route.
        backup_set_canonical_album(
            row["id"], route["backup_chat_id"], new_ids, caption_backup_id
        )
        new_id_set = set(new_ids)
        for old_mid in old_backup_ids:
            if int(old_mid) in new_id_set:
                continue
            try:
                await app.bot.delete_message(
                    chat_id=route["backup_chat_id"], message_id=int(old_mid)
                )
            except Exception:
                LOG.warning("Could not delete old backup message %s", old_mid, exc_info=True)

        if old_record and new_id_set:
            with db() as c:
                c.execute(
                    "DELETE FROM backup_message_map WHERE record_id=? AND backup_message_id NOT IN (%s)"
                    % ",".join("?" for _ in new_id_set),
                    (old_record["id"], *tuple(new_id_set))
                )

    try:
        # Every NEW 888 source album gets a chance to start Follow-up.
        # backup_save_5_index() may classify a genuinely new Code as "updated"
        # when another customer key (phone/name) already exists, so Follow-up
        # must not depend on action == "created". followup_start() itself
        # de-duplicates by Code and therefore will only create one flow per Code.
        try:
            await followup_start(app.bot, row, user)
        except Exception:
            LOG.exception("could not start 888 album followup")

        if action == "created":
            backup_mark_pending(row["id"])
            confirm = await app.bot.send_message(
                chat_id=chat_id,
                text=backup_full_details_message(
                    "✅ 整组相册已备份到 888 backup",
                    row['raw_text'] or (caption_msg.caption if caption_msg else ''),
                    f"🕒 888 原资料 {BACKUP_DELETE_HOURS}小时后自动清理\n"
                    "🟡 状态：待 Admin 确认",
                ),
                reply_markup=backup_confirm_markup(row["id"]),
            )
        else:
            reply_notes = backup_record_reply_notes_text(row['id'])
            confirm = await app.bot.send_message(
                chat_id=chat_id,
                text=backup_full_details_message(
                    f"🔄 整组资料已同步更新｜Code：{row['code'] or '-'}",
                    row['raw_text'] or (caption_msg.caption if caption_msg else ''),
                    f"📝 回复记录：\n{reply_notes}" if reply_notes else "",
                ),
            )
        if action == "updated":
            backup_queue_receipt_delete(chat_id, confirm.message_id, 60)
        else:
            backup_queue_delete(chat_id, confirm.message_id)
    except Exception:
        LOG.exception("album backup status message failed")

    for m in messages:
        backup_queue_delete(chat_id, m.message_id)



# ===== 888 ADMIN RECORD CONFIRMATION =====
CONFIRM_SOURCE_CHAT_ID = -1003941232666

def backup_confirm_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS backup_record_confirmations(
            record_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            confirmed_by INTEGER,
            confirmed_name TEXT,
            confirmed_at TEXT,
            snapshot_json TEXT,
            updated_at TEXT NOT NULL
        )""")

def backup_confirm_markup(record_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认资料", callback_data=f"bconfirm:{int(record_id)}")
    ]])

def backup_mark_pending(record_id):
    if not record_id:
        return
    backup_confirm_init()
    with db() as c:
        c.execute("""INSERT INTO backup_record_confirmations(record_id,status,updated_at)
                     VALUES(?,'pending',?)
                     ON CONFLICT(record_id) DO UPDATE SET
                       status='pending', confirmed_by=NULL, confirmed_name=NULL,
                       confirmed_at=NULL, snapshot_json=NULL, updated_at=excluded.updated_at""",
                  (int(record_id), now_iso()))

def backup_confirmation_status(record_id):
    backup_confirm_init()
    with db() as c:
        row=c.execute("SELECT * FROM backup_record_confirmations WHERE record_id=?", (int(record_id),)).fetchone()
    return dict(row) if row else None


def backup_confirm_source_markup(source_message_id):
    """Initial 888 confirmation button keyed by the original source message.
    This does not depend on the backup record already existing when the album arrives.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认资料", callback_data=f"bconfirmsrc:{int(source_message_id)}")
    ]])

async def backup_confirm_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    if not q or not user:
        return
    try:
        source_message_id = int((q.data or "").split(":", 1)[1])
    except Exception:
        await q.answer("无效资料", show_alert=True)
        return

    # Same permission model as /find admin confirmation.
    if not (user.id == OWNER_ID or bot_admin_has(user.id)):
        await q.answer("只有 Owner / Admin 可以确认资料", show_alert=True)
        return

    # Album backup/indexing can finish a moment after the Admin button appears.
    # Wait briefly inside the click instead of forcing the Admin to retry.
    mapped = backup_lookup_map(CONFIRM_SOURCE_CHAT_ID, source_message_id)
    if not mapped:
        for _ in range(12):  # up to ~6 seconds
            await asyncio.sleep(0.5)
            mapped = backup_lookup_map(CONFIRM_SOURCE_CHAT_ID, source_message_id)
            if mapped:
                break
    if mapped and mapped.get("record_id"):
        record_id = int(mapped["record_id"])
    else:
        # Fallback for older rows where the per-message map was not written.
        with db() as c:
            rr = c.execute("""SELECT id FROM backup_records
                              WHERE source_chat_id=? AND source_message_id=?
                              ORDER BY id DESC LIMIT 1""",
                           (CONFIRM_SOURCE_CHAT_ID, source_message_id)).fetchone()
        if not rr:
            await q.answer("这份资料还没完成入库，请稍后再点", show_alert=True)
            return
        record_id = int(rr["id"])
    # Reuse the canonical record-confirm logic by writing the same snapshot state.
    with db() as c:
        row = c.execute("SELECT * FROM backup_records WHERE id=?", (record_id,)).fetchone()
    if not row:
        await q.answer("找不到这份资料", show_alert=True)
        return

    who = user.full_name or user.username or str(user.id)
    snapshot = dict(row)
    backup_confirm_init()
    with db() as c:
        c.execute("""INSERT INTO backup_record_confirmations(
                     record_id,status,confirmed_by,confirmed_name,confirmed_at,snapshot_json,updated_at
                   ) VALUES(?,'confirmed',?,?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET
                     status='confirmed', confirmed_by=excluded.confirmed_by,
                     confirmed_name=excluded.confirmed_name, confirmed_at=excluded.confirmed_at,
                     snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at""",
                  (record_id, user.id, who, now_iso(), json.dumps(snapshot, ensure_ascii=False, default=str), now_iso()))
    await q.answer("✅ 已确认资料")
    try:
        await q.edit_message_text(
            "✅ 资料已确认\n"
            f"📌 Code：{row['code'] or '-'}\n"
            f"👤 确认人：{who}\n"
            f"🕒 {datetime.now(TZ).strftime('%d/%m/%Y %I:%M %p')}"
        )
    except Exception:
        pass

async def backup_confirm_prompt_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the Admin confirmation button immediately for every NEW 888 record.

    Do not depend on DB indexing or field parsing. Telegram albums are delivered
    as separate updates; only the caption-bearing member gets the button.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or int(chat.id) != int(CONFIRM_SOURCE_CHAT_ID):
        return
    if getattr(update, "edited_message", None) is not None:
        return
    if msg.text and msg.text.startswith("/"):
        return

    # Reply photos supplement existing records; do not create a new confirmation.
    if msg.reply_to_message and msg.photo:
        return

    # Follow Up status replies are workflow commands, never new customer
    # records.  Do not generate a fake Admin-confirm card for 等待中/done/fly.
    if msg.reply_to_message:
        w = _fu_norm(msg.text or msg.caption or "")
        fu_words = ({_fu_norm(x) for x in FOLLOWUP_DONE_WORDS} |
                    {_fu_norm(x) for x in FOLLOWUP_FLY_WORDS} |
                    {_fu_norm(x) for x in FOLLOWUP_WAIT_WORDS})
        if w in fu_words:
            return

    # Albums: the caption-bearing item is the record anchor.
    if msg.media_group_id:
        if not (msg.caption or "").strip():
            return
    else:
        # Standalone records must have meaningful text/caption.
        if not ((msg.text or msg.caption or "").strip()):
            return

    # Persistent de-duplication so one source message gets one confirm prompt.
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS backup_confirm_prompts(
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            prompt_message_id INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY(source_chat_id, source_message_id)
        )""")
        exists = c.execute(
            "SELECT prompt_message_id FROM backup_confirm_prompts WHERE source_chat_id=? AND source_message_id=?",
            (int(chat.id), int(msg.message_id))
        ).fetchone()
    if exists:
        return

    text = (msg.text or msg.caption or "").strip()
    data = backup_parse_5_index(text) or {}
    code = (data.get("code") or "").strip()
    # Admin confirm is a one-time internal record for a NEW Code only.
    # Same customer/code updates do not create another confirmation card.
    if not code:
        return
    with db() as c:
        already = c.execute(
            "SELECT id FROM backup_records WHERE UPPER(TRIM(code))=UPPER(?) ORDER BY id LIMIT 1",
            (code,)
        ).fetchone()
    if already:
        return
    try:
        sent = await context.bot.send_message(
            chat_id=int(chat.id),
            reply_to_message_id=int(msg.message_id),
            text=backup_full_details_message(
                f"🟡 待 Admin 确认资料｜Code：{code}",
                text,
                "业务员可继续修改；Owner / Admin 检查后点确认。",
            ),
            reply_markup=backup_confirm_source_markup(msg.message_id),
        )
        with db() as c:
            c.execute(
                "INSERT OR REPLACE INTO backup_confirm_prompts(source_chat_id,source_message_id,prompt_message_id,created_at) VALUES(?,?,?,?)",
                (int(chat.id), int(msg.message_id), int(sent.message_id), now_iso())
            )
    except Exception:
        LOG.exception("could not send initial 888 admin confirm button")

async def backup_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not q or not user:
        return
    try:
        record_id = int((q.data or "").split(":",1)[1])
    except Exception:
        await q.answer("无效资料", show_alert=True)
        return
    if not (int(user.id) == int(OWNER_ID) or bot_admin_has(user.id)):
        await q.answer("只有 Owner / Admin 可以确认资料", show_alert=True)
        return
    if chat and int(chat.id) != int(CONFIRM_SOURCE_CHAT_ID):
        await q.answer("请在 888 主资料群确认", show_alert=True)
        return
    with db() as c:
        row = c.execute("SELECT * FROM backup_records WHERE id=?", (record_id,)).fetchone()
    if not row:
        await q.answer("找不到这份资料", show_alert=True)
        return
    snapshot = {k: row[k] for k in row.keys()}
    backup_confirm_init()
    import json as _json
    who = user.full_name or user.username or str(user.id)
    ts = now_iso()
    with db() as c:
        c.execute("""INSERT INTO backup_record_confirmations(
                     record_id,status,confirmed_by,confirmed_name,confirmed_at,snapshot_json,updated_at
                   ) VALUES(?,'confirmed',?,?,?,?,?)
                   ON CONFLICT(record_id) DO UPDATE SET
                     status='confirmed', confirmed_by=excluded.confirmed_by,
                     confirmed_name=excluded.confirmed_name, confirmed_at=excluded.confirmed_at,
                     snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at""",
                  (record_id, int(user.id), who, ts, _json.dumps(snapshot, ensure_ascii=False, default=str), ts))
    await q.answer("✅ 已确认资料")
    try:
        old = q.message.text or q.message.caption or ""
        suffix = f"\n\n✅ 已确认资料\n👤 {who}\n🕒 {datetime.now(TZ).strftime('%d/%m/%Y %I:%M %p')}"
        if q.message.text is not None:
            await q.edit_message_text((old + suffix)[:4096])
        elif q.message.caption is not None:
            await q.edit_message_caption(caption=(old + suffix)[:1024])
        else:
            await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

# ===== 888 FOLLOW-UP (locked to the 888 source group only) =====
FOLLOWUP_SOURCE_CHAT_ID = -1003941232666
FOLLOWUP_INTERVAL_HOURS = 1
FOLLOWUP_DONE_WORDS = {"done", "可以", "可以了", "完成", "完成了", "已完成"}
FOLLOWUP_FLY_WORDS = {"飞", "飛", "飞了", "飛了", "fly", "bye", "byebye", "gg"}
FOLLOWUP_WAIT_WORDS = {"等待中", "等待", "等候中", "waiting", "wait"}

def _fu_norm(v):
    return re.sub(r"\s+", "", (v or "").strip().casefold())

def _fu_work_window(now=None):
    now = now or datetime.now(TZ)
    wd = now.weekday()
    if wd == 6:  # Sunday
        return None
    end_hour = 16 if wd == 5 else 18
    return now.replace(hour=10, minute=0, second=0, microsecond=0), now.replace(hour=end_hour, minute=0, second=0, microsecond=0)

def _fu_is_work_time(now=None):
    now = now or datetime.now(TZ)
    win = _fu_work_window(now)
    return bool(win and win[0] <= now < win[1])

def _fu_next_work_time(now=None):
    now = now or datetime.now(TZ)
    for i in range(8):
        d = now + timedelta(days=i)
        win = _fu_work_window(d)
        if not win:
            continue
        start, end = win
        if i == 0 and now < end:
            return max(now, start)
        if i > 0:
            return start
    return now + timedelta(days=1)

def _fu_due_after(now=None):
    now = now or datetime.now(TZ)
    due = now + timedelta(hours=FOLLOWUP_INTERVAL_HOURS)
    if _fu_is_work_time(due):
        return due
    return _fu_next_work_time(due)

def _fu_responsible(sale_person, source_user):
    # Follow-up owner is ALWAYS the Telegram user who actually posted the
    # original 888 record. Do not infer from Sale person; that can tag the
    # wrong person when Admin posts/edits a record.
    return int(source_user.id), (source_user.full_name or source_user.username or str(source_user.id))

def _fu_mention(uid, name, sale_person):
    if uid:
        import html
        return f'<a href="tg://user?id={int(uid)}">{html.escape(name or sale_person or str(uid))}</a>'
    sale = (sale_person or "").strip()
    return sale if sale.startswith("@") else (sale or "业务员")

def followup_notes_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS backup_followup_notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            followup_id INTEGER NOT NULL,
            note_text TEXT NOT NULL,
            author_user_id INTEGER,
            author_name TEXT,
            created_at TEXT NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_backup_followup_notes_fu ON backup_followup_notes(followup_id,id)")


def followup_note_add(followup_id, text, user):
    note=(text or "").strip()
    if not note:
        return
    followup_notes_init()
    author=(getattr(user, "full_name", None) or getattr(user, "username", None) or str(getattr(user, "id", ""))).strip()
    with db() as c:
        c.execute(
            "INSERT INTO backup_followup_notes(followup_id,note_text,author_user_id,author_name,created_at) VALUES(?,?,?,?,?)",
            (int(followup_id), note[:1000], int(user.id) if getattr(user, "id", None) else None, author[:200], now_iso())
        )


def followup_notes_text(followup_id, limit=20):
    followup_notes_init()
    with db() as c:
        rows=c.execute(
            "SELECT note_text,author_name FROM backup_followup_notes WHERE followup_id=? ORDER BY id ASC LIMIT ?",
            (int(followup_id), int(limit))
        ).fetchall()
    if not rows:
        return ""
    lines=[]
    for i,r in enumerate(rows,1):
        note=(r["note_text"] or "").strip()
        author=(r["author_name"] or "-").strip()
        lines.append(f"{i}. {note} — {author}")
    return "\n".join(lines)


def backup_record_reply_notes_text(record_id, limit=100):
    """Return every recorded staff Reply for one 888 record in original order."""
    followup_notes_init()
    backup_record_replies_init()
    with db() as c:
        rows = c.execute(
            """SELECT n.note_text, n.created_at, n.id AS row_id
               FROM backup_followup_notes n
               JOIN backup_followups f ON f.id=n.followup_id
               WHERE f.record_id=?
               UNION ALL
               SELECT r.reply_text AS note_text, r.created_at, r.id AS row_id
               FROM backup_record_replies r
               WHERE r.record_id=?
               ORDER BY created_at, row_id LIMIT ?""",
            (int(record_id), int(record_id), int(limit) * 2),
        ).fetchall()
    # The same Reply may also be a Follow Up note. De-duplicate it while
    # keeping the user's original multiline/numbered text intact.
    found=[]
    seen=set()
    for r in rows:
        note=(r["note_text"] or "").strip()
        key=note.casefold()
        if note and key not in seen:
            seen.add(key)
            found.append(note)
        if len(found) >= int(limit):
            break
    return "\n\n".join(f"{i}. {note}" for i,note in enumerate(found,1))


def backup_record_replies_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS backup_record_replies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            source_chat_id INTEGER NOT NULL,
            reply_message_id INTEGER NOT NULL,
            reply_text TEXT NOT NULL,
            author_user_id INTEGER,
            author_name TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(source_chat_id,reply_message_id)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_backup_record_replies_record ON backup_record_replies(record_id,id)")


def _fu_split_wait_note(raw):
    """Return (is_wait, note_after_keyword) for wait/waiting/等待中 + optional note."""
    raw=(raw or "").strip()
    cf=raw.casefold()
    # Longest first so waiting is not consumed as wait.
    for kw in sorted(FOLLOWUP_WAIT_WORDS, key=lambda x: len(str(x)), reverse=True):
        k=str(kw)
        kcf=k.casefold()
        if cf == kcf:
            return True, ""
        if cf.startswith(kcf):
            rest=raw[len(k):].lstrip(" ：:,-—|/\t")
            if rest:
                return True, rest
    return False, ""


def followup_message_map_init():
    """Map each Follow Up reminder message back to its source record.

    Staff often Reply the bot reminder (not the original album). Without this
    mapping, words like 等待中 can fall through into the generic 888 record
    router and create a fake pending-confirm record with Code '-'.
    """
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS backup_followup_message_map(
            reminder_message_id INTEGER PRIMARY KEY,
            followup_id INTEGER NOT NULL,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""")


async def followup_send(bot, row, immediate=False):
    if not _fu_is_work_time():
        return False
    # Keep only the newest reminder visible for this follow-up.
    followup_message_map_init()
    try:
        with db() as c:
            prev = c.execute("""SELECT reminder_message_id FROM backup_followup_message_map
                                WHERE followup_id=? AND source_chat_id=?
                                ORDER BY created_at DESC LIMIT 1""",
                             (int(row["id"]), int(row["source_chat_id"]))).fetchone()
        if prev:
            try:
                await bot.delete_message(chat_id=int(row["source_chat_id"]),
                                         message_id=int(prev["reminder_message_id"]))
            except Exception:
                pass
            with db() as c:
                c.execute("DELETE FROM backup_followup_message_map WHERE reminder_message_id=?",
                          (int(prev["reminder_message_id"]),))
    except Exception:
        LOG.exception("could not clear previous followup reminder id=%s", row["id"])

    import html as _html
    who = _fu_mention(row["responsible_user_id"], row["responsible_name"], row["sale_person"])
    code = row["code"] or "资料"
    state = "\n🕐 状态：等待中" if row["status"] == "waiting" else ""
    notes = backup_record_reply_notes_text(row["record_id"])
    notes_block = f"\n\n📝 跟进记录：\n{_html.escape(notes)}" if notes else ""
    with db() as c:
        record = c.execute(
            "SELECT raw_text FROM backup_records WHERE id=?",
            (int(row["record_id"] or 0),),
        ).fetchone()
    full_details = _html.escape(((record["raw_text"] if record else "") or "-").strip())
    prefix = (
        f"⏰ Follow Up｜{code}\n"
        f"👤 {who}{state}\n\n"
        "📋 完整资料：\n"
    )
    suffix = (
        f"{notes_block}\n\n"
        "1. 现在能拿吗？\n"
        "2. 资料确认完了吗？\n"
        "3. 现在处理到哪里了？\n\n"
        "请 Reply 原资料或这条提醒：\n"
        "• done / 可以 / 完成（不用追问）\n"
        "• 等待中 / wait / waiting + 备注（继续追问）\n"
        "• 也可以直接写问题 / 备注 / 处理进度（继续追问）\n"
        "• 飞了 / fly / bye / byebye / gg（不用追问；也支持 飞 / 飛 / 飛了）"
    )
    room = max(0, 4096 - len(prefix) - len(suffix))
    if len(full_details) > room:
        full_details = full_details[:max(0, room - 5)] + "\n…"
    text = prefix + full_details + suffix
    sent = await bot.send_message(
        chat_id=int(row["source_chat_id"]), text=text, parse_mode="HTML",
        reply_to_message_id=int(row["source_message_id"]), allow_sending_without_reply=True
    )
    now = datetime.now(TZ)
    followup_message_map_init()
    with db() as c:
        c.execute("UPDATE backup_followups SET last_reminded_at=?,next_reminder_at=?,updated_at=? WHERE id=?",
                  (now.isoformat(), _fu_due_after(now).isoformat(), now_iso(), row["id"]))
        c.execute("""INSERT OR REPLACE INTO backup_followup_message_map(
                     reminder_message_id,followup_id,source_chat_id,source_message_id,created_at
                   ) VALUES(?,?,?,?,?)""",
                  (int(sent.message_id), int(row["id"]), int(row["source_chat_id"]),
                   int(row["source_message_id"]), now_iso()))
    return True

async def followup_start(bot, row, source_user):
    if int(row["source_chat_id"] or 0) != FOLLOWUP_SOURCE_CHAT_ID:
        return
    # Follow-up is only for a NEW customer/new Code. Same Code later edits,
    # replacement photos, parcel updates or extra information keep the same flow.
    code=(row["code"] or "").strip()
    if not code:
        return
    with db() as c:
        existing=c.execute(
            "SELECT id FROM backup_followups WHERE source_chat_id=? AND UPPER(TRIM(code))=UPPER(?) ORDER BY id LIMIT 1",
            (FOLLOWUP_SOURCE_CHAT_ID, code)
        ).fetchone()
    if existing:
        return

    uid, uname = _fu_responsible(row["sale_person"], source_user)
    now = datetime.now(TZ)
    with db() as c:
        c.execute(
            """INSERT INTO backup_followups(record_id,source_chat_id,source_message_id,code,sale_person,responsible_user_id,responsible_name,status,next_reminder_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,'pending',?,?,?)
               ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET
                 record_id=excluded.record_id, code=excluded.code, sale_person=excluded.sale_person,
                 responsible_user_id=excluded.responsible_user_id, responsible_name=excluded.responsible_name, updated_at=excluded.updated_at""",
            (row["id"], FOLLOWUP_SOURCE_CHAT_ID, int(row["source_message_id"]), code, row["sale_person"] or "", uid, uname,
             _fu_next_work_time(now).isoformat(), now.isoformat(), now.isoformat())
        )
        fu = c.execute("SELECT * FROM backup_followups WHERE source_chat_id=? AND source_message_id=?",
                       (FOLLOWUP_SOURCE_CHAT_ID, int(row["source_message_id"]))).fetchone()
    # Starts immediately during working hours; then every 1 hour.
    if fu and fu["status"] not in ("done", "fly") and _fu_is_work_time(now):
        await followup_send(bot, fu, immediate=True)

async def followup_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or not user or int(chat.id) != FOLLOWUP_SOURCE_CHAT_ID or not msg.reply_to_message:
        return
    raw=(msg.text or msg.caption or "").strip()
    if not raw or raw.startswith("/"):
        return
    target_mid = int(msg.reply_to_message.message_id)

    # Resolve reply to original record, any album member, or the latest bot reminder.
    with db() as c:
        fu = c.execute("SELECT * FROM backup_followups WHERE source_chat_id=? AND source_message_id=?",
                       (FOLLOWUP_SOURCE_CHAT_ID, target_mid)).fetchone()
        if not fu:
            try:
                followup_message_map_init()
                fm = c.execute("""SELECT followup_id FROM backup_followup_message_map
                                  WHERE reminder_message_id=? AND source_chat_id=? LIMIT 1""",
                               (target_mid, FOLLOWUP_SOURCE_CHAT_ID)).fetchone()
                if fm:
                    fu = c.execute("SELECT * FROM backup_followups WHERE id=?",
                                   (int(fm["followup_id"]),)).fetchone()
            except Exception:
                fu = None
        if not fu:
            mp = c.execute("SELECT record_id FROM backup_message_map WHERE source_chat_id=? AND source_message_id=?",
                           (FOLLOWUP_SOURCE_CHAT_ID, target_mid)).fetchone()
            if mp and mp["record_id"]:
                fu = c.execute("SELECT * FROM backup_followups WHERE source_chat_id=? AND record_id=? ORDER BY id DESC LIMIT 1",
                               (FOLLOWUP_SOURCE_CHAT_ID, mp["record_id"])).fetchone()
    if not fu:
        return

    norm=_fu_norm(raw)
    done_set={_fu_norm(x) for x in FOLLOWUP_DONE_WORDS}
    fly_set={_fu_norm(x) for x in FOLLOWUP_FLY_WORDS}
    is_wait, wait_note=_fu_split_wait_note(raw)

    if norm in done_set:
        status="done"
        label="✅ 可以拿 / 已完成，Follow Up 已停止"
        note=""
    elif norm in fly_set:
        status="fly"
        label="❌ 已结束 / 飞了，Follow Up 已停止"
        note=""
    elif is_wait:
        status="waiting"
        note=wait_note
        label="🕐 已读 · 等待中，1小时后继续提醒"
    else:
        # Any member can record a question/note/progress by replying to this
        # follow-up. It becomes the next reminder context and keeps the flow active.
        status="waiting"
        note=raw
        label="📝 已记录跟进，1小时后继续提醒"

    if note:
        followup_note_add(fu["id"], note, user)

    nxt = None if status in ("done", "fly") else _fu_due_after(datetime.now(TZ)).isoformat()
    with db() as c:
        c.execute("UPDATE backup_followups SET status=?,next_reminder_at=?,updated_at=? WHERE id=?",
                  (status, nxt, now_iso(), fu["id"]))

    who=(user.full_name or user.username or str(user.id))
    if note:
        await msg.reply_text(f"{label}\n📝 {note} — {who}")
    else:
        await msg.reply_text(label)

    if status in ("done", "fly"):
        try:
            followup_message_map_init()
            with db() as c:
                prev = c.execute("""SELECT reminder_message_id FROM backup_followup_message_map
                                    WHERE followup_id=? AND source_chat_id=?
                                    ORDER BY created_at DESC LIMIT 1""",
                                 (int(fu["id"]), FOLLOWUP_SOURCE_CHAT_ID)).fetchone()
            if prev:
                try:
                    await context.bot.delete_message(chat_id=FOLLOWUP_SOURCE_CHAT_ID,
                                                     message_id=int(prev["reminder_message_id"]))
                except Exception:
                    pass
                with db() as c:
                    c.execute("DELETE FROM backup_followup_message_map WHERE reminder_message_id=?",
                              (int(prev["reminder_message_id"]),))
        except Exception:
            LOG.exception("could not clear terminal followup reminder id=%s", fu["id"])
    raise ApplicationHandlerStop

async def followup_worker(app):
    while True:
        try:
            now = datetime.now(TZ)
            if _fu_is_work_time(now):
                with db() as c:
                    rows = c.execute(
                        "SELECT * FROM backup_followups WHERE source_chat_id=? AND status IN ('pending','waiting') AND next_reminder_at IS NOT NULL AND next_reminder_at<=? ORDER BY id LIMIT 50",
                        (FOLLOWUP_SOURCE_CHAT_ID, now.isoformat())
                    ).fetchall()
                for r in rows:
                    try:
                        await followup_send(app.bot, r)
                    except Exception:
                        LOG.exception("888 followup reminder failed id=%s", r["id"])
            await asyncio.sleep(60)
        except Exception:
            LOG.exception("888 followup worker error")
            await asyncio.sleep(60)


async def backup_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """All staff can Reply + cancel a single 888 source record.
    Backup destination and /find index remain intact.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    text = (msg.text or "").strip().casefold()
    if text not in ("取消", "cancel", "取消备份", "取消備份"):
        return

    route = backup_get_route()
    if not route or int(route["source_chat_id"] or 0) != int(chat.id):
        return

    target = msg.reply_to_message
    if not target:
        await msg.reply_text("⚠️ 请 Reply 要取消的那一笔资料")
        return

    source_ids = {int(target.message_id)}

    try:
        with db() as c:
            row = c.execute(
                """SELECT record_id FROM backup_message_map
                   WHERE source_chat_id=? AND source_message_id=? LIMIT 1""",
                (int(chat.id), int(target.message_id))
            ).fetchone()

            if row and row["record_id"]:
                rows = c.execute(
                    """SELECT source_message_id FROM backup_message_map
                       WHERE source_chat_id=? AND record_id=?
                       ORDER BY source_message_id""",
                    (int(chat.id), int(row["record_id"]))
                ).fetchall()
                mapped = {
                    int(r["source_message_id"])
                    for r in rows if r["source_message_id"]
                }
                if mapped:
                    source_ids = mapped
    except Exception:
        LOG.exception("single cancel mapping lookup failed")

    deleted = 0
    failed = 0
    for mid in sorted(source_ids):
        try:
            await context.bot.delete_message(
                chat_id=int(chat.id),
                message_id=int(mid)
            )
            deleted += 1
            try:
                sos_forget_source_message(chat.id, mid)
            except Exception:
                pass
        except Exception as e:
            failed += 1
            LOG.error(
                "SINGLE_CANCEL_DELETE_FAIL chat=%s message_id=%s error=%r",
                chat.id, mid, e
            )

    try:
        await msg.delete()
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(chat.id),
            text=(
                "↩️ 单笔取消完成\n"
                f"✅ 已删除：{deleted} 条\n"
                f"⚠️ 删除失败：{failed} 条\n"
                "🔒 888 backup 保留\n"
                "🔎 /find 仍可找回"
            )
        )
    except Exception:
        pass



def backup_supplements_init():
    # Supplements are separate from the canonical album: replacing an album
    # must never delete separately supplied supporting photos.
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS backup_supplements(
            source_chat_id INTEGER NOT NULL, source_message_id INTEGER NOT NULL,
            record_id INTEGER NOT NULL, backup_chat_id INTEGER NOT NULL,
            backup_message_id INTEGER NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(source_chat_id,source_message_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS backup_find_result_map(
            chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,record_id INTEGER NOT NULL,
            PRIMARY KEY(chat_id,message_id))""")

def backup_record_for_message(msg):
    """Resolve an exact message mapping first, then an unambiguous Code field."""
    if msg is None:
        return None
    backup_supplements_init()
    chat_id = getattr(msg, 'chat_id', None) or getattr(getattr(msg, 'chat', None), 'id', None)
    mid = getattr(msg, 'message_id', None)
    if chat_id is None or mid is None:
        return None
    with db() as c:
        record_ids = set()
        for sql, params in (
            ("SELECT record_id FROM backup_supplements WHERE (source_chat_id=? AND source_message_id=?) OR (backup_chat_id=? AND backup_message_id=?)", (chat_id,mid,chat_id,mid)),
            ("SELECT record_id FROM backup_message_map WHERE (source_chat_id=? AND source_message_id=?) OR (backup_chat_id=? AND backup_message_id=?)", (chat_id,mid,chat_id,mid)),
            ("SELECT record_id FROM backup_find_result_map WHERE chat_id=? AND message_id=?", (chat_id,mid)),
            ("SELECT id AS record_id FROM backup_records WHERE (source_chat_id=? AND source_message_id=?) OR (backup_chat_id=? AND backup_message_id=?)", (chat_id,mid,chat_id,mid)),
        ):
            for row in c.execute(sql, params).fetchall():
                if row['record_id'] is not None:
                    record_ids.add(int(row['record_id']))
        if len(record_ids) == 1:
            row = c.execute('SELECT * FROM backup_records WHERE id=?', (next(iter(record_ids)),)).fetchone()
            return dict(row) if row else None
        if record_ids:
            return None
        # Code fallback only in the locked source/backup groups, never use names
        # or a guessed number to attach one person's photo to another record.
        if int(chat_id) not in (BACKUP_PRESERVE_SOURCE_ID, BACKUP_PRESERVE_TARGET_ID):
            return None
        body = getattr(msg, 'text', None) or getattr(msg, 'caption', None) or ''
        match = re.search(r'(?im)^\s*Code\s*[:：]\s*([A-Za-z]{1,4}[ /-]*\d{4})\s*$', body)
        if not match:
            return None
        key = re.sub(r'[^A-Za-z0-9]', '', match.group(1)).upper()
        rows = c.execute("""SELECT * FROM backup_records WHERE source_chat_id=? AND
            UPPER(REPLACE(REPLACE(REPLACE(code,' ',''),'-',''),'/',''))=? LIMIT 2""",
            (BACKUP_PRESERVE_SOURCE_ID,key)).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None


async def backup_record_reply_capture_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Attach every meaningful 888 Reply to the exact record it replied to."""
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if (not msg or not chat or not user or getattr(user, 'is_bot', False)
            or int(chat.id) != BACKUP_PRESERVE_SOURCE_ID or not msg.reply_to_message):
        return
    raw = (msg.text or msg.caption or '').strip()
    if not raw or raw.startswith('/'):
        return
    if raw.casefold() in {'取消','取消备份','取消備份','cancel'}:
        return

    record = backup_record_for_message(msg.reply_to_message)
    record_id = int(record['id']) if record else 0
    if not record_id:
        # A Follow Up reminder is a Bot message, so resolve it through its
        # exact reminder mapping instead of guessing from visible text.
        try:
            followup_message_map_init()
            with db() as c:
                mapped = c.execute(
                    """SELECT f.record_id
                       FROM backup_followup_message_map m
                       JOIN backup_followups f ON f.id=m.followup_id
                       WHERE m.source_chat_id=? AND m.reminder_message_id=? LIMIT 1""",
                    (int(chat.id), int(msg.reply_to_message.message_id)),
                ).fetchone()
            record_id = int(mapped['record_id']) if mapped and mapped['record_id'] else 0
        except Exception:
            record_id = 0
    if not record_id:
        return

    norm = _fu_norm(raw)
    terminal = ({_fu_norm(x) for x in FOLLOWUP_DONE_WORDS} |
                {_fu_norm(x) for x in FOLLOWUP_FLY_WORDS})
    if norm in terminal:
        return
    is_wait, wait_note = _fu_split_wait_note(raw)
    if is_wait:
        raw = wait_note.strip()
        if not raw:
            return

    backup_record_replies_init()
    author = user.full_name or user.username or str(user.id)
    with db() as c:
        c.execute(
            """INSERT OR IGNORE INTO backup_record_replies(
                 record_id,source_chat_id,reply_message_id,reply_text,
                 author_user_id,author_name,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (record_id, int(chat.id), int(msg.message_id), raw[:4000],
             int(user.id), author[:200], now_iso()),
        )

async def backup_copy_reply_photos(app, messages, chat_id):
    """Append reply photos to their existing record without changing its fields."""
    if int(chat_id) != BACKUP_PRESERVE_SOURCE_ID:
        return False
    route = backup_get_route()
    if not backup_preserve_route_enabled(route):
        return False
    photos = [m for m in messages if getattr(m, 'photo', None)]
    replies = [m for m in photos if getattr(m, 'reply_to_message', None)]
    if not replies:
        return False
    resolved = [backup_record_for_message(m.reply_to_message) for m in replies]
    if any(r is None for r in resolved) or len({r['id'] for r in resolved}) != 1:
        await replies[0].reply_text('⚠️ 补图未备份：无法确定对应资料，请 Reply 原资料或已关联的照片重新发送。')
        return True
    record = resolved[0]
    if int(record.get('source_chat_id') or 0) != BACKUP_PRESERVE_SOURCE_ID:
        await replies[0].reply_text('⚠️ 补图未备份：回复的资料不属于 888。')
        return True
    backup_supplements_init()
    count = 0
    for msg in sorted(photos, key=lambda m: m.message_id):
        with db() as c:
            existing = c.execute('SELECT 1 FROM backup_supplements WHERE source_chat_id=? AND source_message_id=?',
                                 (int(chat_id),int(msg.message_id))).fetchone()
        if existing:
            continue
        try:
            copied = await app.bot.copy_message(
                chat_id=BACKUP_PRESERVE_TARGET_ID, from_chat_id=int(chat_id),
                message_id=int(msg.message_id),
                caption=backup_redact_full_text(getattr(msg, 'caption', None) or '')[:1024],
                reply_to_message_id=int(record['backup_message_id']) if record.get('backup_message_id') else None,
                allow_sending_without_reply=True)
            with db() as c:
                c.execute('INSERT INTO backup_supplements VALUES(?,?,?,?,?,?)',
                          (int(chat_id),int(msg.message_id),int(record['id']),BACKUP_PRESERVE_TARGET_ID,
                           int(copied.message_id),now_iso()))
            count += 1
        except Exception:
            LOG.exception('888 reply photo copy failed source=%s', msg.message_id)
            await msg.reply_text('⚠️ 此张补图备份失败，请 Reply 原资料重发这张图片。')
    if count:
        await replies[0].reply_text(f"✅ 已关联备份 {count} 张补图｜Code：{record.get('code') or '-'}")
    return True

async def backup_edit_reply_photo(update, context):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not chat or int(chat.id) != BACKUP_PRESERVE_SOURCE_ID:
        return False
    if not backup_preserve_route_enabled(backup_get_route()):
        return False
    backup_supplements_init()
    with db() as c:
        row = c.execute('SELECT * FROM backup_supplements WHERE source_chat_id=? AND source_message_id=?',
                        (int(chat.id),int(msg.message_id))).fetchone()
    if not row:
        return False
    try:
        if msg.photo:
            await context.bot.edit_message_media(
                chat_id=int(row['backup_chat_id']),message_id=int(row['backup_message_id']),
                media=InputMediaPhoto(media=msg.photo[-1].file_id,
                                      caption=backup_redact_full_text(msg.caption or '')[:1024]))
            await msg.reply_text('🔄 补图已同步修改')
    except Exception:
        LOG.exception('888 supplement edit failed')
        await msg.reply_text('⚠️ 补图同步修改失败，请稍后重试。')
    return True

async def backup_direct_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dedicated Backup router.
    Runs before Lead/status handlers and supports:
    - text
    - single photo + caption
    - multi-photo albums + caption
    """
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or chat.type == "private":
        return

    if not backup_route_matches_source(chat.id):
        return

    # IMPORTANT: edited album messages must be handled only by
    # backup_edited_handler. If they enter the normal album buffer, the one
    # edited member can be mistaken for a brand-new one-photo album and
    # replace/delete the canonical Backup album.
    if getattr(update, "edited_message", None) is not None:
        return

    # Album: collect all items first, then copy the whole album.
    if msg.media_group_id:
        key = (chat.id, msg.media_group_id)
        item = BACKUP_ALBUM_BUFFER.setdefault(
            key,
            {"chat_id": chat.id, "messages": [], "user": user},
        )
        # avoid duplicate update insertion
        if not any(x.message_id == msg.message_id for x in item["messages"]):
            item["messages"].append(msg)

        old_task = BACKUP_ALBUM_TASKS.get(key)
        if old_task and not old_task.done():
            old_task.cancel()
        BACKUP_ALBUM_TASKS[key] = asyncio.create_task(
            backup_flush_album(context.application, key)
        )
        raise ApplicationHandlerStop

    # Reply photos belong to the existing record, not a new blank record.
    if await backup_copy_reply_photos(context.application, [msg], chat.id):
        raise ApplicationHandlerStop

    # Normal text or single photo/caption.
    handled = await backup_message_handler(update, context.application)
    if handled:
        raise ApplicationHandlerStop

async def backup_edited_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mirror edits from 888 to the exact mapped Backup message.

    Rules for the locked 888 route:
    - caption/text edit -> update only text/caption;
    - photo replacement -> replace only that mapped Backup photo;
    - never delete/rebuild the surrounding album for an edit;
    - image-only edits keep the existing indexed NAME/NRIC/Code instead of
      overwriting them with blanks.
    """
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    if not backup_route_matches_source(chat.id):
        return

    if await backup_edit_reply_photo(update, context):
        raise ApplicationHandlerStop

    mapped = backup_lookup_map(chat.id, msg.message_id)
    if not mapped:
        return

    text = msg.text or msg.caption or ""
    parsed = backup_parse_5_index(text) if text.strip() else None
    safe_text = backup_redact_full_text(text) if text.strip() else ""
    record_id = int(mapped["record_id"])

    # Keep the exact source-message -> backup-message mapping.  An edit must
    # never rebuild/delete the album because that is what previously caused
    # 5 photos to become 1/4 photos after a correction.
    try:
        if msg.photo:
            latest = msg.photo[-1]
            old_unique = (mapped.get("source_file_unique_id") or "") if isinstance(mapped, dict) else ""
            new_unique = latest.file_unique_id or ""
            photo_changed = (not old_unique) or (old_unique != new_unique)

            if photo_changed:
                # Source photo itself changed: replace ONLY this exact mapped Backup item.
                # Every other member of the Telegram album stays untouched.
                media = InputMediaPhoto(
                    media=latest.file_id,
                    caption=(safe_text[:1024] if (msg.caption or "").strip() else None),
                )
                await context.bot.edit_message_media(
                    chat_id=int(mapped["backup_chat_id"]),
                    message_id=int(mapped["backup_message_id"]),
                    media=media,
                )
            else:
                # Same photo, caption/text only changed: NEVER touch/rebuild the media.
                await context.bot.edit_message_caption(
                    chat_id=int(mapped["backup_chat_id"]),
                    message_id=int(mapped["backup_message_id"]),
                    caption=(safe_text[:1024] if (msg.caption or "").strip() else None),
                )

            # Refresh the fingerprint so the next edit is compared against the
            # actual current 888 photo rather than guessed from album position.
            backup_message_map_media_columns()
            with db() as c:
                c.execute(
                    """UPDATE backup_message_map
                       SET source_file_unique_id=?, source_file_id=?, updated_at=?
                       WHERE source_chat_id=? AND source_message_id=?""",
                    (new_unique, latest.file_id, now_iso(), int(chat.id), int(msg.message_id)),
                )
        elif msg.text is not None:
            await context.bot.edit_message_text(
                chat_id=int(mapped["backup_chat_id"]),
                message_id=int(mapped["backup_message_id"]),
                text=safe_text[:4096],
            )
        else:
            return
    except Exception:
        LOG.exception("sync edited source item to exact backup item failed")
        return

    # Only rewrite searchable/indexed fields when the edited message still
    # contains a real data caption.  Image-only edits must preserve the prior
    # customer fields instead of turning them into '-'.
    row = None
    if parsed:
        _action, row = backup_save_5_index(
            parsed,
            chat.id,
            user.id,
            user.full_name or user.username or str(user.id),
            msg.message_id,
            mapped["backup_chat_id"],
            mapped["backup_message_id"],
        )
        record_id = int(row["id"])
    else:
        with db() as c:
            row = c.execute("SELECT * FROM backup_records WHERE id=?", (record_id,)).fetchone()

    # Edits are normal corrections/supplements for the same customer.
    # Admin confirmation is a one-time internal record for a new Code only,
    # so editing text/photo must not create another confirmation card.
    try:
        code = (row["code"] if row else None) or "-"
        reply_notes = backup_record_reply_notes_text(row['id']) if row else ""
        receipt = await msg.reply_text(
            backup_full_details_message(
                f"🔄 Backup 已自动同步修改｜Code：{code}",
                (row['raw_text'] if row else '') or text,
                f"📝 回复记录：\n{reply_notes}" if reply_notes else "",
            )
        )
        backup_queue_receipt_delete(chat.id, receipt.message_id, 60)
    except Exception:
        pass

    raise ApplicationHandlerStop


async def backup_cleanup_worker(app):
    while True:
        with db() as c:
            rows = c.execute(
                "SELECT * FROM backup_cleanup_queue WHERE delete_after<=? ORDER BY id LIMIT 100",
                (now_iso(),)
            ).fetchall()
        for r in rows:
            try:
                await app.bot.delete_message(chat_id=r["chat_id"], message_id=r["message_id"])
            except Exception:
                pass
            with db() as c:
                c.execute("DELETE FROM backup_cleanup_queue WHERE id=?", (r["id"],))
        await asyncio.sleep(5)


async def universal_group_id_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Absolute-priority /group_id router for any private/group/supergroup chat.
    It is intentionally independent of all Lead/Backup/Calculator route logic.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    text = (msg.text or msg.caption or "").strip()
    if not re.match(r"^/group_id(?:@\w+)?(?:\s.*)?$", text, re.I):
        return

    route_name = "未绑定 / 未识别"
    # Company-account groups are fixed routes. They share the same accounting
    # features, but every ledger row/query remains isolated by this chat_id.
    if int(chat.id) == -1004482054615:
        route_name = "公司账本｜公司帐"
    elif int(chat.id) == -1004444989940:
        route_name = "公司账本｜裕鑫 and 财富"
    try:
        r = backup_get_route()
        if r:
            src = r["source_chat_id"] if "source_chat_id" in r.keys() else None
            dst = r["backup_chat_id"] if "backup_chat_id" in r.keys() else None
            if src is not None and int(src) == int(chat.id):
                route_name = "888 主资料群 / Backup Source"
            elif dst is not None and int(dst) == int(chat.id):
                route_name = "888 Backup / Backup Target"
    except Exception:
        pass

    title = getattr(chat, "title", None) or "Private Chat"
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                "📍 Group Information\n"
                f"群名：{title}\n"
                f"Group ID：{chat.id}\n"
                f"Route：{route_name}"
            ),
            reply_to_message_id=msg.message_id,
        )
    except Exception:
        # Retry without reply threading in case topic/reply restrictions apply.
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "📍 Group Information\n"
                    f"群名：{title}\n"
                    f"Group ID：{chat.id}\n"
                    f"Route：{route_name}"
                ),
            )
        except Exception:
            pass
    raise ApplicationHandlerStop


async def group_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the current Telegram chat ID without changing any route/config."""
    chat = update.effective_chat
    if not chat:
        return

    route_name = "未绑定 / 未识别"
    if int(chat.id) == -1004482054615:
        route_name = "公司账本｜公司帐"
    elif int(chat.id) == -1004444989940:
        route_name = "公司账本｜裕鑫 and 财富"
    try:
        r = backup_get_route()
        if r:
            if r["source_chat_id"] and int(r["source_chat_id"]) == int(chat.id):
                route_name = "888 主资料群 / Backup Source"
            elif r["backup_chat_id"] and int(r["backup_chat_id"]) == int(chat.id):
                route_name = "888 Backup / Backup Target"
    except Exception:
        pass

    title = getattr(chat, "title", None) or "Private Chat"
    await update.effective_message.reply_text(
        "📍 Group Information\n"
        f"群名：{title}\n"
        f"Group ID：{chat.id}\n"
        f"Route：{route_name}"
    )


async def backup_main_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    chat = update.effective_chat
    set_setting("backup_main_chat_id", chat.id)
    set_setting("backup_main_id", chat.id)
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在 888 主资料群使用 /backup_main")
        return
    backup_set_route(source_chat_id=chat.id)
    pset("backup_source_chat_id", chat.id)
    await update.effective_message.reply_text(
        "✅ 已永久设为 Backup 主资料群\n"
        f"主资料群ID：{chat.id}"
    )


async def backup_here_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在 888 backup 群使用 /backup_here")
        return
    backup_set_route(backup_chat_id=chat.id)
    pset("backup_target_chat_id", chat.id)
    await update.effective_message.reply_text(
        "✅ 已永久设为 Backup 群\n"
        f"Backup群ID：{chat.id}"
    )


async def backup_route_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = backup_get_route()
    src = r["source_chat_id"] if r and r["source_chat_id"] else "未设置"
    dst = r["backup_chat_id"] if r and r["backup_chat_id"] else "未设置"
    ok = bool(r and r["source_chat_id"] and r["backup_chat_id"])

    await update.effective_message.reply_text(
        "🗂 Backup 路由\n"
        f"主资料群：{src}\n"
        f"Backup群：{dst}\n"
        f"持久设置：{'✅ 已完成' if ok else '⚠️ 未完成'}"
    )


async def start_backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user = update.effective_chat, update.effective_user
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在 Backup 业务群里发送 /start_backup")
        return
    if user.id != OWNER_ID:
        await update.effective_message.reply_text("❌ 只有 Owner 可以启动 Backup 群。")
        return
    with db() as c:
        c.execute(
            """INSERT INTO backup_groups(chat_id,title,enabled,created_at)
               VALUES(?,?,1,?)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, enabled=1""",
            (chat.id, chat.title or str(chat.id), now_iso())
        )
    await update.effective_message.reply_text(
        f"✅ Backup 已启动\n🕒 群消息 {BACKUP_DELETE_HOURS}小时后自动清理\n🗄 后台记录继续保留"
    )

async def stop_backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    with db() as c:
        c.execute("UPDATE backup_groups SET enabled=0 WHERE chat_id=?", (update.effective_chat.id,))
    await update.effective_message.reply_text("⏸ Backup 已暂停")


def find_normalize_phone(value):
    """Normalize phone text for lookup only.
    Examples:
      014-304 0468 -> 60143040468
      0143040468   -> 60143040468
      +60143040468 -> 60143040468
    """
    if value is None:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return ""

    if digits.startswith("0"):
        digits = "60" + digits[1:]
    elif digits.startswith("1"):
        # local mobile entered without leading zero
        digits = "60" + digits
    elif digits.startswith("60"):
        pass

    return digits


def find_strip_field_prefix(value):
    """Allow /find Customer:..., Phone:..., Tel:... etc."""
    q = (value or "").strip()
    q = re.sub(
        r"^\s*(?:customer|phone|tel|telephone|mobile|self)\s*:\s*",
        "",
        q,
        flags=re.I,
    )
    return q.strip()


def backup_find_phone_rows(raw_query, limit=20):
    """Search Customer/Self phone in current and legacy Backup indexes."""
    clean = find_strip_field_prefix(raw_query)
    normalized = find_normalize_phone(clean)
    if not normalized:
        return []

    local = "0" + normalized[2:] if normalized.startswith("60") else normalized
    targets = {normalized, local}

    def digits_only(v):
        return re.sub(r"\D", "", str(v or ""))

    with db() as c:
        tables = []
        for t in ("backup_records","backup_5_index","backup_index","backup_search_index","backup_items"):
            try:
                cols = [r["name"] for r in c.execute(f"PRAGMA table_info({t})").fetchall()]
            except Exception:
                cols = []
            if cols:
                tables.append((t, cols))

        found = []
        seen = set()

        for table, cols in tables:
            try:
                rows = c.execute(
                    f"SELECT * FROM {table} ORDER BY " +
                    ("id DESC" if "id" in cols else "rowid DESC") +
                    " LIMIT 1000"
                ).fetchall()
            except Exception:
                continue

            for row in rows:
                d = dict(row)

                # Search explicit phone fields plus any stored original text.
                values = []
                for col in ("customer","phone","self_phone","self","full_text","text"):
                    if col in d and d.get(col):
                        values.append(d.get(col))

                matched = False
                for v in values:
                    dv = digits_only(v)
                    if not dv:
                        continue
                    # Match either local 0-prefix or normalized 60-prefix.
                    dv_norm = find_normalize_phone(dv)
                    if dv in targets or dv_norm in targets:
                        matched = True
                        break

                    # Also inspect full text line-by-line for Customer/Self numbers.
                    for cand in re.findall(r"\+?\d[\d\-\s]{7,16}\d", str(v)):
                        c_norm = find_normalize_phone(cand)
                        if c_norm in targets:
                            matched = True
                            break
                    if matched:
                        break

                if not matched:
                    continue

                key = (
                    d.get("id"),
                    d.get("backup_message_id"),
                    d.get("message_id"),
                    d.get("media_group_id"),
                    table,
                )
                if key in seen:
                    continue
                seen.add(key)
                d["_find_table"] = table
                found.append(d)

                if len(found) >= int(limit):
                    return found

        return found



def backup_888_search(query, limit=20):
    """Case/whitespace-insensitive lookup over the existing searchable records."""
    q = re.sub(r"(?i)^\s*(?:name|姓名|名字|nric|code|sale\s*person|customer|phone|self)\s*[:：]\s*", "", query or "").strip()
    if not q:
        return []
    def fold(value):
        return re.sub(r"\s+", "", str(value or "")).casefold()
    key = fold(q)
    code_key = re.sub(r"[ /\\-]", "", key)
    with db() as c:
        cols = _backup_table_columns(c, "backup_records")
        if not cols:
            return []
        c.create_function("find_fold", 1, fold)
        searchable = [col for col in ("code", "name", "nric", "customer", "self_value", "sale_person",
                       "raw_text", "address", "acc_no", "card_number", "bank", "area") if col in cols]
        clauses = [f"INSTR(find_fold({col}), ?) > 0" for col in searchable]
        params = [key] * len(clauses)
        if 'code' in cols and code_key:
            clauses.append("REPLACE(REPLACE(REPLACE(find_fold(code),'-',''),'/',''),'\\','')=?")
            params.append(code_key)
        if not clauses:
            return []
        order = 'id DESC' if 'id' in cols else 'rowid DESC'
        rows = c.execute(f"SELECT * FROM backup_records WHERE ({' OR '.join(clauses)}) ORDER BY {order} LIMIT ?",
                         tuple(params) + (int(limit),)).fetchall()
        return [dict(row) for row in rows]

async def backup_find_query(update: Update, context: ContextTypes.DEFAULT_TYPE, q: str):
    """Shared /find executor used by slash and plain-text find aliases."""
    if not find_auth_has(update.effective_user.id):
        await update.effective_message.reply_text("⛔ 你没有 /find 查询权限")
        return

    q = (q or "").strip()
    # Phone-aware lookup: spaces, hyphens, +60/0 and Customer: prefix are equivalent.
    raw_find_query = q
    cleaned_find_query = find_strip_field_prefix(raw_find_query)
    looks_like_phone = bool(re.search(r"\d{7,}", re.sub(r"\D", "", cleaned_find_query)))

    if looks_like_phone:
        phone_rows = backup_find_phone_rows(raw_find_query, 20)
        if phone_rows:
            # Reuse the existing result-rendering path by replacing the search result
            # variable later if possible. Store on context for a safe fallback branch.
            context.chat_data["_phone_find_rows"] = phone_rows

    if not q:
        await update.effective_message.reply_text(
            "用法：find Code / /find Code / NAME / NRIC / Customer电话 / Self电话 / Sale person"
        )
        return

    rows = context.chat_data.pop("_phone_find_rows", None)
    if rows is None:
        if int(update.effective_chat.id) in (BACKUP_PRESERVE_SOURCE_ID, BACKUP_PRESERVE_TARGET_ID):
            rows = backup_888_search(q)
        else:
            rows = backup_search(q)
    if not rows:
        await update.effective_message.reply_text("🔎 找不到记录")
        return

    for i, row in enumerate(rows[:5], 1):
        await update.effective_message.reply_text(
            backup_full_details_message(
                f"🔎 查询结果 {i}/{min(len(rows),5)}｜Code：{row['code'] or '-'}",
                row.get('raw_text') or (
                    f"📌 NAME : {row['name'] or '-'}\n"
                    f"📌 NRIC : {row['nric'] or '-'}\n"
                    f"Customer : {row['customer'] or '-'}\n"
                    f"Sale person : {row['sale_person'] or '-'}\n"
                    f"Code : {row['code'] or '-'}"
                ),
            )
        )

        canonical = backup_get_canonical_album(row["id"])
        if not canonical or not canonical["message_ids"]:
            canonical = backup_largest_contiguous_message_set(row["id"])
            if canonical and canonical["message_ids"]:
                backup_set_canonical_album(
                    row["id"],
                    canonical["backup_chat_id"],
                    canonical["message_ids"],
                    canonical.get("caption_message_id"),
                )

        backup_supplements_init()
        batches = []
        if canonical and canonical["message_ids"]:
            batches.append((canonical["backup_chat_id"], canonical["message_ids"]))
        elif row.get("backup_chat_id") and row.get("backup_message_id"):
            batches.append((int(row["backup_chat_id"]), [int(row["backup_message_id"])]))
        with db() as c:
            supplements = c.execute(
                "SELECT backup_chat_id,backup_message_id FROM backup_supplements WHERE record_id=? ORDER BY source_message_id",
                (int(row["id"]),)).fetchall()
        for supplement in supplements:
            batches.append((int(supplement["backup_chat_id"]), [int(supplement["backup_message_id"])]))
        seen = set()
        for from_chat, message_ids in batches:
            ids = sorted({int(mid) for mid in message_ids if (int(from_chat), int(mid)) not in seen})
            seen.update((int(from_chat),mid) for mid in ids)
            for offset in range(0,len(ids),100):
                chunk = ids[offset:offset+100]
                try:
                    copied = await context.bot.copy_messages(
                        chat_id=update.effective_chat.id, from_chat_id=int(from_chat),message_ids=chunk)
                    with db() as c:
                        for item in copied:
                            c.execute("INSERT OR REPLACE INTO backup_find_result_map VALUES(?,?,?)",
                                      (int(update.effective_chat.id),int(item.message_id),int(row["id"])))
                    if len(copied) != len(chunk):
                        await update.effective_message.reply_text("⚠️ 部分备份图片已无法读取，以上结果不完整。")
                except Exception:
                    LOG.exception("backup find media copy failed")
                    await update.effective_message.reply_text("⚠️ 部分备份图片读取失败，请稍后重试。")

        # Staff Reply notes belong at the very bottom of each /find result,
        # after all photos. Preserve their original multiline/numbered layout.
        reply_notes = backup_record_reply_notes_text(row["id"])
        if reply_notes:
            await update.effective_message.reply_text(
                f"📝 回复记录｜Code：{row['code'] or '-'}\n\n{reply_notes}"[:4096]
            )


def backup_find_query_from_reply(msg):
    """When staff replies to a Code/record and types only find, use the replied text."""
    if not msg or not msg.reply_to_message:
        return ""
    row = backup_record_for_message(msg.reply_to_message)
    if row and row.get("code"):
        return row["code"]
    src = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    if not src:
        return ""

    # Prefer an explicit Code field from a full customer record.
    m = re.search(r"^\s*Code\s*:\s*([^\n\r]+)", src, flags=re.I | re.M)
    if m:
        return m.group(1).strip()

    # Then a short bank-code token such as M4674 / AG7471 / HLB1234.
    m = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{1,4}[\s/\-]*\d{4})(?!\d)", src)
    if m:
        return m.group(1).strip()

    # For a short one-line reply (NAME / phone / NRIC etc.), use it directly.
    one = re.sub(r"\s+", " ", src).strip()
    if len(one) <= 120 and "\n" not in src and "\r" not in src:
        return one
    return ""


async def backup_find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    if not q:
        q = backup_find_query_from_reply(update.effective_message)
    await backup_find_query(update, context, q)



def backup_888_reply_query(msg):
    """Read a reply mapping or Telegram selected quote, without global chat state."""
    parent = getattr(msg, "reply_to_message", None)
    if parent:
        try:
            row = backup_record_for_message(parent)
            if row and row.get("code"):
                return row["code"]
        except Exception:
            # A historical mapping issue must not prevent text/quote lookup.
            LOG.exception("888 find reply mapping unavailable")
    sources = []
    if parent:
        sources.append(getattr(parent, "text", None) or getattr(parent, "caption", None) or "")
    quote = getattr(msg, "quote", None)
    if quote:
        sources.append(getattr(quote, "text", None) or "")
    for source in sources:
        # Accept both Code: and Code： inside a bot confirmation, not just line start.
        match = re.search(r"(?i)(?<![A-Za-z])Code\s*[:：]\s*([A-Za-z]{1,4}[ /-]*\d{4})(?![A-Za-z0-9])", source)
        if match:
            return match.group(1).strip()
    for source in sources:
        match = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{1,4}[ /-]*\d{4})(?![A-Za-z0-9])", source)
        if match:
            return match.group(1).strip()
    for source in reversed(sources):
        source = source.strip()
        if source and len(source) <= 120 and "\n" not in source and "\r" not in source:
            return source
    return ""

async def backup_888_find_priority_router(update, context):
    """Only the two 888 groups: find must precede follow-up/note handlers."""
    chat, msg = update.effective_chat, update.effective_message
    if not chat or not msg or int(chat.id) not in (BACKUP_PRESERVE_SOURCE_ID, BACKUP_PRESERVE_TARGET_ID):
        return
    text = (msg.text or "").strip()
    match = re.fullmatch(r"/?find(?:@\w+)?(?:(?:\s+|\s*[:：]\s*)(.+))?", text, re.I)
    if not match:
        return
    try:
        query = (match.group(1) or "").strip()
        if not query and find_auth_has(update.effective_user.id):
            query = backup_888_reply_query(msg)
        await backup_find_query(update, context, query)
    except ApplicationHandlerStop:
        raise
    except Exception:
        LOG.exception("888 find request failed")
        await msg.reply_text("⚠️ 查询失败，请稍后重试；可输入 find C5219，或 Reply 原资料后发送 find。")
    # Never fall through to follow-up, backup or a second /find handler.
    raise ApplicationHandlerStop

async def backup_find_flexible_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept find or /find in any capitalization for convenience.

    Examples:
      find AG7471
      FIND ag7471
      Find AG7471
      /find AG7471
      /FIND ag7471
    """
    msg = update.effective_message
    if not msg:
        return
    text = (msg.text or "").strip()
    m = re.match(r"^\s*/?find(?:@\w+)?(?:\s*[:：]\s*|\s+)?(.*?)\s*$", text, flags=re.I)
    if not m:
        return
    q = (m.group(1) or "").strip()
    if not q:
        q = backup_find_query_from_reply(msg)
    await backup_find_query(update, context, q)
    raise ApplicationHandlerStop


async def backup_template_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🏦 BANK :\n"
        "📌 NAME :\n"
        "📌 NRIC :\n"
        "📌 ADDRESS :\n"
        "💳 ACC NO :\n"
        "💳 CARD NUMBER :\n"
        "💳 EXP DATE :\n"
        "💳 C的VV :\n"
        "——————————————————\n"
        "Nama Ibu :\n"
        "PIN :\n"
        "O9 Username :\n"
        "O9 Pas :\n"
        "Cawangan OPENING :\n"
        "——————————————————\n\n"
        "Self :\n"
        "Customer :\n"
        "Lala/Post :\n\n"
        "Sale person :\n"
        "Area :\n"
        "Code :\n"
        "loan/反诈 :"
    )


async def backup_today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_dt = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    with db() as c:
        total = c.execute("SELECT COUNT(*) n FROM backup_records WHERE created_at>=?",
                          (start_dt.isoformat(),)).fetchone()["n"]
    await update.effective_message.reply_text(
        f"📊 Backup 今日统计\n🇲🇾 {datetime.now(TZ):%Y-%m-%d}\n\n📥 新记录：{total}"
    )

async def backup_message_handler(update: Update, app: Application):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not chat or not user or chat.type == "private":
        return False

    route = backup_get_route()
    if not route or not route["source_chat_id"] or not route["backup_chat_id"]:
        return False
    if chat.id != route["source_chat_id"]:
        return False

    text = msg.text or msg.caption or ""
    data = backup_parse_5_index(text)
    if not data:
        return False

    safe_text = backup_redact_full_text(text)
    mapped = backup_lookup_map(chat.id, msg.message_id)

    has_media = bool(
        getattr(msg, "photo", None) or getattr(msg, "video", None) or
        getattr(msg, "animation", None) or getattr(msg, "document", None) or
        getattr(msg, "audio", None) or getattr(msg, "voice", None) or
        getattr(msg, "video_note", None) or getattr(msg, "sticker", None)
    )
    meaningful_index = any(str(v or "").strip() for v in (data or {}).values())

    # Pure media / ordinary-caption media must still be preserved in 888 backup.
    # Do not manufacture an empty customer record merely because a video/file
    # has no NAME/NRIC/Code fields.
    if has_media and not meaningful_index and not mapped:
        try:
            backup_message_id = await backup_copy_single_message(app, route, msg, safe_text)
            with db() as c:
                c.execute(
                    """INSERT INTO backup_message_map(
                       source_chat_id,source_message_id,backup_chat_id,backup_message_id,
                       record_id,created_at,updated_at
                       ) VALUES(?,?,?,?,NULL,?,?)
                       ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET
                         backup_chat_id=excluded.backup_chat_id,
                         backup_message_id=excluded.backup_message_id,
                         updated_at=excluded.updated_at""",
                    (int(chat.id), int(msg.message_id), int(route["backup_chat_id"]),
                     int(backup_message_id), now_iso(), now_iso()),
                )
            backup_queue_delete(chat.id, msg.message_id)
            return True
        except Exception:
            LOG.exception("888 media passthrough to backup failed")
            await msg.reply_text("⚠️ Backup 媒体复制失败，请确认 Bot 在 backup 群有发送媒体权限。")
            return True

    if mapped:
        # Source message was edited: update the existing permanent backup copy.
        try:
            if msg.photo:
                # Existing album item: edit caption only. Do not touch the media
                # and do not rebuild/delete the surrounding album.
                await app.bot.edit_message_caption(
                    chat_id=mapped["backup_chat_id"],
                    message_id=mapped["backup_message_id"],
                    caption=safe_text[:1024] if (msg.caption or "").strip() else "",
                )
            else:
                await app.bot.edit_message_text(
                    chat_id=mapped["backup_chat_id"],
                    message_id=mapped["backup_message_id"],
                    text=safe_text[:4096],
                )
        except Exception:
            LOG.exception("backup sync edit failed")
        action, row = backup_save_5_index(
            data, chat.id, user.id,
            user.full_name or user.username or str(user.id),
            msg.message_id,
            mapped["backup_chat_id"], mapped["backup_message_id"]
        )
        backup_mark_pending(row["id"])
        await msg.reply_text(
            backup_full_details_message(
                f"🔄 Backup 已同步修改｜Code：{row['code'] or '-'}",
                row['raw_text'] or text,
                "🟡 状态：资料有修改，等待 Admin 再确认",
            ),
            reply_markup=backup_confirm_markup(row["id"]),
        )
        return True

    # New source message: copy media/text to permanent backup group.
    try:
        if has_media:
            backup_message_id = await backup_copy_single_message(app, route, msg, safe_text)
        else:
            sent = await app.bot.send_message(
                chat_id=route["backup_chat_id"],
                text=safe_text[:4096],
            )
            backup_message_id = sent.message_id
    except Exception:
        LOG.exception("copy to backup group failed")
        await msg.reply_text("⚠️ Backup 复制失败，请确认 Bot 已在 Backup 群并有发消息权限。")
        return True

    action, row = backup_save_5_index(
        data, chat.id, user.id,
        user.full_name or user.username or str(user.id),
        msg.message_id,
        route["backup_chat_id"], backup_message_id
    )

    # Same rule for single-photo/text records: a new Code may be classified
    # as an update because another indexed customer field already exists.
    # Always let followup_start() decide whether this Code needs a new flow.
    try:
        await followup_start(app.bot, row, user)
    except Exception:
        LOG.exception("could not start 888 followup")

    if action == "created":
        backup_mark_pending(row["id"])
        confirm = await msg.reply_text(
            backup_full_details_message(
                "✅ 已备份到 888 backup",
                row['raw_text'] or text,
                f"🕒 本群资料 {BACKUP_DELETE_HOURS}小时后自动清理\n"
                "🟡 状态：待 Admin 确认",
            ),
            reply_markup=backup_confirm_markup(row["id"]),
        )
        backup_queue_delete(chat.id, confirm.message_id)
    else:
        reply_notes = backup_record_reply_notes_text(row['id'])
        synced = await msg.reply_text(
            backup_full_details_message(
                f"🔄 Backup 已同步更新｜Code：{row['code'] or '-'}",
                row['raw_text'] or text,
                f"📝 回复记录：\n{reply_notes}" if reply_notes else "",
            )
        )
        backup_queue_receipt_delete(chat.id, synced.message_id, 60)

    backup_queue_delete(chat.id, msg.message_id)
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    role = "Owner" if uid == OWNER_ID else ("助理" if allowed(uid) else "未授权")
    await update.effective_message.reply_text(
        "📥 Lead Bot V1 · 自动分配版\n\n"
        f"你的 ID：{uid}\n身份：{role}\n\n"
        "流程：入口群贴 Lead → 自动查重复 → 新号码自动 A/B/C 轮流分配\n\n"
        "设置：\n/start_lead 启动当前入口群（只需一次）\n/stop_lead 暂停自动分配\n/team_here 团队名称 设当前群为接收群\n/teams 查看团队\n/report 查看报表\n/help 查看全部指令"
    )


async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Lead Bot · ANTISCAM-AD-FILTER · 2026-09-11"
    )


async def version_nodrop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Lead Bot V1 STATUS-CANCEL 2026-08-18")


async def staff_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 Lead Bot 业务员指令 / Staff Commands\n\n"
        "⚠️ 请先 Reply 客户 Lead，再输入：\n"
        "⚠️ Reply to the customer's Lead first, then send:\n\n"
        "✅ 中过了 / 中過了 / Used Before\n"
        "🚔 警察 / Police\n"
        "❌ 没WS / 沒WS / 无效 / 無效 / No WhatsApp / No WS\n"
        "💰 成交 / Deal\n"
        "↩️ 取消 / 取消状态 / 取消狀態 / Cancel\n\n"
        "🔄 标记错了：Reply 原 Lead，直接输入正确状态，会自动覆盖。\n"
        "🔄 Wrong status: Reply to the original Lead and enter the correct status; it will replace the old one.\n\n"
        "⚠️ 没有 Reply Lead，不会更改客户状态。\n"
        "⚠️ Without replying to a Lead, no customer status will be changed."
    )


async def report_test_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("✅ Report handler OK")


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🕚 自动日报：每天 11:59 PM\n"
        "🇲🇾 Malaysia Time · Asia/Kuala_Lumpur\n"
        "📊 /today 今日｜/report YYYY-MM-DD 日报｜/week YYYY-MM-DD 周报｜/month YYYY-MM 月报"
    )




def route_key_from_title(title):
    k = str(title or "").strip().casefold()
    if "loan" in k or "贷款" in k or "貸款" in k:
        return "loan"
    return "antiscam"

def inbox_route_set(chat_id, title, route_key=None, enabled=1):
    route_key = route_key or route_key_from_title(title)
    with db() as c:
        c.execute(
            """INSERT INTO lead_inbox_routes(chat_id,route_key,title,enabled,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 route_key=excluded.route_key,
                 title=excluded.title,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (int(chat_id), str(route_key), title or str(chat_id), int(enabled), now_iso())
        )
    return str(route_key)

def inbox_route_get(chat_id):
    with db() as c:
        row = c.execute(
            """SELECT route_key,title,enabled
               FROM lead_inbox_routes WHERE chat_id=?""",
            (int(chat_id),)
        ).fetchone()
    if row and int(row["enabled"]) == 1:
        return str(row["route_key"])
    return None

def team_route_set(chat_id, route_key):
    with db() as c:
        c.execute(
            """INSERT INTO lead_team_routes(chat_id,route_key,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 route_key=excluded.route_key,
                 updated_at=excluded.updated_at""",
            (int(chat_id), str(route_key), now_iso())
        )

def team_route_get(chat_id, team_name=""):
    with db() as c:
        row = c.execute(
            "SELECT route_key FROM lead_team_routes WHERE chat_id=?",
            (int(chat_id),)
        ).fetchone()
    if row:
        return str(row["route_key"])
    # Compatibility for already configured groups:
    # Loan A/Loan B/... => loan; everything else => antiscam.
    return route_key_from_title(team_name)

def lead_inbox_get():
    with db() as c:
        return c.execute(
            "SELECT chat_id, enabled FROM lead_inbox_config WHERE id=1"
        ).fetchone()


def lead_inbox_is_active(chat_id):
    # New multi-inbox route table is the source of truth.
    if inbox_route_get(chat_id):
        return True

    # Legacy single-inbox compatibility: migrate it on first use.
    row = lead_inbox_get()
    if row and row["chat_id"] == chat_id and int(row["enabled"]) == 1:
        title = get_setting("inbox_name", str(chat_id))
        inbox_route_set(chat_id, title, route_key_from_title(title), 1)
        return True

    old_id = get_setting("inbox_chat_id")
    enabled = get_setting("lead_auto_enabled", "0")
    if old_id and int(old_id) == int(chat_id) and str(enabled) == "1":
        title = get_setting("inbox_name", str(chat_id))
        inbox_route_set(chat_id, title, route_key_from_title(title), 1)
        return True

    return False


def lead_inbox_set(chat_id, enabled=1):
    with db() as c:
        c.execute(
            """INSERT INTO lead_inbox_config(id,chat_id,enabled,updated_at)
               VALUES(1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 chat_id=excluded.chat_id,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (chat_id, int(enabled), now_iso()),
        )



def pset(key, value):
    with db() as c:
        c.execute(
            """INSERT INTO persistent_settings(key,value,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 updated_at=excluded.updated_at""",
            (str(key), json.dumps(value, ensure_ascii=False), now_iso()),
        )

def pget(key, default=None):
    with db() as c:
        row = c.execute(
            "SELECT value FROM persistent_settings WHERE key=?",
            (str(key),),
        ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]

def persistent_team_add(chat_id, title, bucket="A"):
    bucket = (bucket or "A").upper()
    with db() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(sort_order),-1)+1 n FROM persistent_team_groups WHERE bucket=?",
            (bucket,),
        ).fetchone()
        order = int(row["n"])
        c.execute(
            """INSERT INTO persistent_team_groups(
               chat_id,bucket,title,enabled,sort_order,created_at,updated_at
            ) VALUES(?,?,?,1,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
               bucket=excluded.bucket,
               title=excluded.title,
               enabled=1,
               updated_at=excluded.updated_at""",
            (int(chat_id), bucket, title or str(chat_id), order, now_iso(), now_iso()),
        )

def persistent_teams(bucket="A"):
    bucket = (bucket or "A").upper()
    with db() as c:
        return c.execute(
            """SELECT chat_id,bucket,title,sort_order
               FROM persistent_team_groups
               WHERE enabled=1 AND bucket=?
               ORDER BY sort_order,chat_id""",
            (bucket,),
        ).fetchall()

def persistent_rr_pick(bucket="A"):
    bucket = (bucket or "A").upper()
    teams = persistent_teams(bucket)
    if not teams:
        return None
    with db() as c:
        row = c.execute(
            "SELECT next_index FROM persistent_rr_state WHERE bucket=?",
            (bucket,),
        ).fetchone()
        idx = int(row["next_index"]) if row else 0
        chosen = teams[idx % len(teams)]
        c.execute(
            """INSERT INTO persistent_rr_state(bucket,next_index,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(bucket) DO UPDATE SET
                 next_index=excluded.next_index,
                 updated_at=excluded.updated_at""",
            (bucket, (idx + 1) % len(teams), now_iso()),
        )
    return chosen

def backup_set_canonical_album(record_id, backup_chat_id, message_ids, caption_message_id=None):
    ids = [int(x) for x in message_ids if x is not None]
    with db() as c:
        c.execute(
            """INSERT INTO backup_album_sets(
               record_id,backup_chat_id,message_ids_json,caption_message_id,updated_at
            ) VALUES(?,?,?,?,?)
            ON CONFLICT(record_id) DO UPDATE SET
               backup_chat_id=excluded.backup_chat_id,
               message_ids_json=excluded.message_ids_json,
               caption_message_id=excluded.caption_message_id,
               updated_at=excluded.updated_at""",
            (
                int(record_id), int(backup_chat_id),
                json.dumps(ids),
                int(caption_message_id) if caption_message_id else None,
                now_iso(),
            ),
        )

def backup_get_canonical_album(record_id):
    with db() as c:
        row = c.execute(
            """SELECT backup_chat_id,message_ids_json,caption_message_id
               FROM backup_album_sets WHERE record_id=?""",
            (record_id,),
        ).fetchone()
    if not row:
        return None
    try:
        ids = [int(x) for x in json.loads(row["message_ids_json"] or "[]")]
    except Exception:
        ids = []
    return {
        "backup_chat_id": int(row["backup_chat_id"]),
        "message_ids": ids,
        "caption_message_id": row["caption_message_id"],
    }

async def set_inbox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在 Lead 入口群使用 /set_inbox")
        return
    lead_inbox_set(chat.id, 1)
    set_setting("inbox_chat_id", chat.id)
    set_setting("inbox_name", chat.title or str(chat.id))
    set_setting("lead_auto_enabled", "1")
    pset("lead_inbox_chat_id", chat.id)
    pset("lead_inbox_enabled", 1)
    await update.effective_message.reply_text(
        "✅ 已永久设为 Lead 入口群\n"
        f"入口群ID：{chat.id}\n"
        "以后重新部署不需要再设置。"
    )


async def inbox_debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    row = lead_inbox_get()
    inbox_id = row["chat_id"] if row and row["chat_id"] else None
    enabled = int(row["enabled"]) if row else 0
    await update.effective_message.reply_text(
        "🧪 Inbox Debug\n"
        f"当前群ID：{chat.id if chat else 'N/A'}\n"
        f"入口群ID：{inbox_id if inbox_id else '未设置'}\n"
        f"入口状态：{enabled}\n"
        "规则：入口群内任何成员均可提交 Lead"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📘 Lead Bot V1 · 自动分配版\n\n"
        "入口群：\n/start_lead（第一次启动一次即可）\n/stop_lead（暂停）\n/inbox 查看入口群\n\n"
        "团队群：\n/team_here 团队A（在目标群发送）\n/delteam 群ID\n/teams\n\n"
        "助理：\n/addassistant 数字ID（也可回复对方消息）\n/delassistant 数字ID\n/assistants\n\n"
        "测试/报表：\n/lead 姓名 | 电话 | 来源\n/report\n\n"
        "✅ 在入口群直接粘贴含电话号码的 Lead，不需要 /lead。\n"
        "✅ 号码历史永久查重；重复号码会注明 DUPLICATE 并照样继续分配。"
    )


async def add_assistant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update, owner=True):
        return
    uid, name = extract_user_id(update, context.args)
    if not uid:
        await update.effective_message.reply_text("用法：/addassistant 数字ID，或回复助理的一条消息后发送 /addassistant")
        return
    with db() as c:
        c.execute("INSERT OR REPLACE INTO assistants(user_id,name,added_at) VALUES(?,?,?)", (uid, name, now_iso()))
    await update.effective_message.reply_text(f"✅ 已授权助理：{name}（{uid}）")


async def del_assistant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update, owner=True):
        return
    uid, _ = extract_user_id(update, context.args)
    if not uid:
        await update.effective_message.reply_text("用法：/delassistant 数字ID")
        return
    with db() as c:
        c.execute("DELETE FROM assistants WHERE user_id=?", (uid,))
    await update.effective_message.reply_text(f"✅ 已取消助理权限：{uid}")


async def assistants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update, owner=True):
        return
    with db() as c:
        rows = c.execute("SELECT * FROM assistants ORDER BY added_at").fetchall()
    text = "\n".join(f"• {r['name']} — {r['user_id']}" for r in rows) or "暂无助理"
    await update.effective_message.reply_text("👥 助理列表\n" + text)


async def inbox_here(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if chat.type == "private":
        await update.effective_message.reply_text("请在『放 Lead 进来的入口群』里发送 /inbox_here。")
        return
    # An inbox must not simultaneously be a distribution target.
    with db() as c:
        c.execute("DELETE FROM teams WHERE chat_id=?", (chat.id,))
    set_setting("inbox_chat_id", chat.id)
    set_setting("inbox_name", chat.title or str(chat.id))
    set_setting("lead_auto_enabled", "1")
    await update.effective_message.reply_text(
        f"✅ 已设为 Lead 入口群：{chat.title or chat.id}\n"
        f"群 ID：{chat.id}\n\n"
        "以后直接把含电话号码的 Lead 发到这里，机器人会自动查重并分配。"
    )



async def start_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在 Loan 入口群发送 /start_loan")
        return

    inbox_route_set(chat.id, chat.title or str(chat.id), "loan", 1)
    set_setting("lead_auto_enabled", "1")
    await update.effective_message.reply_text(
        f"✅ Loan 入口已绑定\nRoute：loan\n群 ID：{chat.id}"
    )


async def start_antiscam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在反诈入口群发送 /start_antiscam")
        return

    inbox_route_set(chat.id, chat.title or str(chat.id), "antiscam", 1)
    set_setting("lead_auto_enabled", "1")
    await update.effective_message.reply_text(
        f"✅ 反诈入口已绑定\nRoute：antiscam\n群 ID：{chat.id}"
    )


async def team_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return

    bucket = (context.args[0].strip().upper() if context.args else "A")
    if bucket not in ("A", "B", "C"):
        await update.effective_message.reply_text("用法：/team_loan A 或 B 或 C")
        return

    name = f"Loan {bucket}"
    with db() as c:
        old = c.execute("SELECT position FROM teams WHERE chat_id=?", (chat.id,)).fetchone()
        pos = old["position"] if old else c.execute(
            "SELECT COALESCE(MAX(position),-1)+1 n FROM teams"
        ).fetchone()["n"]
        c.execute(
            """INSERT OR REPLACE INTO teams(chat_id,name,enabled,position,created_at)
               VALUES(?,?,1,?,?)""",
            (chat.id, name, pos, now_iso())
        )

    team_route_set(chat.id, "loan")
    await update.effective_message.reply_text(
        f"✅ Loan 团队已绑定：{bucket}\nRoute：loan\n群 ID：{chat.id}"
    )


async def team_antiscam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return

    bucket = (context.args[0].strip().upper() if context.args else "A")
    if bucket not in ("A", "B", "C"):
        await update.effective_message.reply_text("用法：/team_antiscam A 或 B 或 C")
        return

    name = f"Antiscam {bucket}"
    with db() as c:
        old = c.execute("SELECT position FROM teams WHERE chat_id=?", (chat.id,)).fetchone()
        pos = old["position"] if old else c.execute(
            "SELECT COALESCE(MAX(position),-1)+1 n FROM teams"
        ).fetchone()["n"]
        c.execute(
            """INSERT OR REPLACE INTO teams(chat_id,name,enabled,position,created_at)
               VALUES(?,?,1,?,?)""",
            (chat.id, name, pos, now_iso())
        )

    team_route_set(chat.id, "antiscam")
    await update.effective_message.reply_text(
        f"✅ 反诈团队已绑定：{bucket}\nRoute：antiscam\n群 ID：{chat.id}"
    )


async def start_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if chat.type == "private":
        await update.effective_message.reply_text("请在 Lead 入口群里发送 /start_lead。")
        return

    route_key = inbox_route_set(
        chat.id,
        chat.title or str(chat.id),
        route_key_from_title(chat.title or ""),
        1,
    )

    # Keep legacy keys synchronized for old code, but do not delete other inboxes.
    set_setting("inbox_chat_id", chat.id)
    set_setting("inbox_name", chat.title or str(chat.id))
    set_setting("lead_auto_enabled", "1")

    route_label = "Loan" if route_key == "loan" else "反诈"
    await update.effective_message.reply_text(
        f"✅ {route_label} Lead 入口已启动\n"
        f"Route：{route_key}\n"
        f"群 ID：{chat.id}\n\n"
        "这个入口只会分配到同 Route 的团队群。"
    )


async def stop_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if chat.type == "private":
        return
    route = inbox_route_get(chat.id)
    if not route:
        await update.effective_message.reply_text("这个群目前不是 Lead 入口群。")
        return
    with db() as c:
        c.execute(
            "UPDATE lead_inbox_routes SET enabled=0,updated_at=? WHERE chat_id=?",
            (now_iso(), int(chat.id))
        )
    await update.effective_message.reply_text("⏸ 当前 Lead 入口已暂停。")


async def inbox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    cid = get_setting("inbox_chat_id")
    name = get_setting("inbox_name", "未命名")
    if not cid:
        await update.effective_message.reply_text("⚠️ 还没有设置入口群。请在入口群发送 /inbox_here")
    else:
        await update.effective_message.reply_text(f"📥 当前入口群：{name}\n群 ID：{cid}")


async def team_here(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    chat = update.effective_chat
    if chat.type == "private":
        await update.effective_message.reply_text("请在接收 Lead 的团队群里发送这条指令。")
        return
    inbox_id = get_setting("inbox_chat_id")
    if inbox_id and int(inbox_id) == chat.id:
        await update.effective_message.reply_text("❌ 这个群目前是 Lead 入口群，不能同时作为 A/B/C 接收群。")
        return
    name = " ".join(context.args).strip() or chat.title or str(chat.id)
    with db() as c:
        old = c.execute("SELECT position FROM teams WHERE chat_id=?", (chat.id,)).fetchone()
        if old:
            pos = old["position"]
        else:
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 n FROM teams").fetchone()["n"]
        c.execute("INSERT OR REPLACE INTO teams(chat_id,name,enabled,position,created_at) VALUES(?,?,1,?,?)",
                  (chat.id, name, pos, now_iso()))
    persistent_team_add(chat.id, name, "A")
    route_key = route_key_from_title(name)
    team_route_set(chat.id, route_key)
    await update.effective_message.reply_text(
        f"✅ 已加入分配：{name}\n"
        f"Route：{route_key}\n"
        f"群 ID：{chat.id}"
    )


async def del_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update, owner=True):
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text("用法：/delteam 群ID")
        return
    cid = int(context.args[0])
    with db() as c:
        c.execute("DELETE FROM teams WHERE chat_id=?", (cid,))
    await update.effective_message.reply_text(f"✅ 已删除团队：{cid}")


async def teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    with db() as c:
        rows = c.execute("SELECT * FROM teams WHERE enabled=1 ORDER BY position,created_at").fetchall()
    text = "\n".join(f"{i+1}. {r['name']} — {r['chat_id']}" for i, r in enumerate(rows)) or "暂无团队群"
    await update.effective_message.reply_text("🏢 Round Robin 团队顺序\n" + text)


async def wa_check(phone: str) -> str:
    if not WA_CHECK_URL:
        return "unknown"
    try:
        headers = {"Authorization": f"Bearer {WA_CHECK_TOKEN}"} if WA_CHECK_TOKEN else {}
        async with ClientSession() as s:
            async with s.post(WA_CHECK_URL, json={"phone": phone}, headers=headers, timeout=15) as r:
                data = await r.json(content_type=None)
                value = data.get("registered", data.get("exists", data.get("valid")))
                return "yes" if value is True else ("no" if value is False else "unknown")
    except Exception:
        LOG.exception("WhatsApp check failed")
        return "unknown"


def select_team(route_key="antiscam"):
    route_key = "loan" if str(route_key).casefold() == "loan" else "antiscam"

    with db() as c:
        all_teams = c.execute(
            "SELECT * FROM teams WHERE enabled=1 ORDER BY position,created_at"
        ).fetchall()

        if not all_teams:
            return None

        teams = []
        for r in all_teams:
            rk = team_route_get(r["chat_id"], r["name"])
            if rk == route_key:
                teams.append(r)

        if not teams:
            return None

        rr_key = f"rr_index_{route_key}"
        row = c.execute("SELECT value FROM settings WHERE key=?", (rr_key,)).fetchone()
        idx = int(row["value"]) if row else 0
        idx %= len(teams)
        team = teams[idx]

        c.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            (rr_key, str((idx + 1) % len(teams))),
        )
        return dict(team)


def _source_created_iso(value):
    """Normalize a source Lead timestamp for permanent historical reporting."""
    x = str(value or "").strip()
    if not x:
        return None
    try:
        # Facebook/Excel ISO timestamps may end in Z or include an offset.
        dt = datetime.fromisoformat(x.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        else:
            dt = dt.astimezone(TZ)
        return dt.isoformat(timespec="seconds")
    except Exception:
        # Accept a plain YYYY-MM-DD as Malaysia-local midnight.
        try:
            dt = datetime.strptime(x[:10], "%Y-%m-%d").replace(tzinfo=TZ)
            return dt.isoformat(timespec="seconds")
        except Exception:
            return None


def save_lead_message_map(chat_id, message_id, lead_id, phone):
    try:
        with db() as c:
            c.execute(
                "INSERT OR REPLACE INTO lead_message_map(chat_id,message_id,lead_id,phone,created_at) VALUES(?,?,?,?,?)",
                (int(chat_id), int(message_id), int(lead_id), str(phone), now_iso()),
            )
    except Exception:
        LOG.exception("save lead message map failed")


async def process_lead(app: Application, data: dict, submitted_by=0, notify_duplicate_owner=True):
    raw_phone = str(data.get("phone") or data.get("phone_number") or data.get("mobile") or "")
    phone = normalize_phone(raw_phone)
    name = str(data.get("name") or data.get("full_name") or data.get("customer_name") or "未填写").strip()
    source = str(data.get("source") or data.get("form_name") or data.get("campaign") or "Unknown").strip()
    entry_chat_id = data.get("entry_chat_id")

    # Resolve route + fixed-schema state BEFORE any logic uses it.
    # Previous builds referenced _fixed_antiscam_occurrence before assignment,
    # which made duplicate-confirm callbacks fail at send time.
    route_key = inbox_route_get(entry_chat_id) if entry_chat_id else None
    if not route_key:
        route_key = str(data.get("route_key") or "").strip().casefold()
    if route_key not in ("loan", "antiscam"):
        raise RuntimeError(f"Lead route missing for entry_chat_id={entry_chat_id}; refusing cross-route fallback")

    _raw_occurrence = str(data.get("raw_text") or "").strip()
    _fixed_antiscam_occurrence = bool(
        route_key == "antiscam"
        and _raw_occurrence
        and _s_schema_text(_raw_occurrence) is not None
    )

    # For the manual anti-scam intake, the CURRENT pasted/filtered Lead is the
    # authority for Customer Name. A phone may legitimately appear again under
    # a different name, so never let an older occurrence overwrite this one.
    if _fixed_antiscam_occurrence:
        try:
            _current_phone = _s_normalize_phone(raw_phone) or normalize_phone(raw_phone)
            _current_fields = _s_manual_fields(_raw_occurrence, _current_phone)
            _current_name = str(_current_fields.get("name") or "").strip()
            if _current_name and _current_name not in ("-", "未填写", "未填寫", "未确认", "未確認", "Unconfirmed"):
                name = _current_name
                data["name"] = _current_name
                data["full_name"] = _current_name
        except Exception:
            LOG.exception("recover current manual Lead name failed")

    # Fixed-schema anti-scam intake uses two different keys for two different jobs:
    #   1) Code/external_id identifies the SAME submitted record. If the same Code
    #      is pasted again, do not create another DB row, do not distribute again,
    #      and do not inflate today's "来过"/ad statistics.
    #   2) Phone identifies the same CUSTOMER across different records/Codes. A new
    #      Code with an old phone is a genuine historical duplicate ("来过").
    external_id = str(data.get("external_id") or "").strip()
    if _fixed_antiscam_occurrence and external_id:
        with db() as c:
            same_code = c.execute(
                """SELECT id,phone,team_name,created_at,source,payload
                   FROM leads WHERE external_id=? ORDER BY id DESC LIMIT 1""",
                (external_id,),
            ).fetchone()
        if same_code:
            return {
                "ok": True,
                "lead_id": int(same_code["id"]),
                "status": "same_code",
                "phone": str(same_code["phone"] or phone),
                "team": same_code["team_name"],
                "duplicate": None,
                "external_id": external_id,
            }

    with db() as c:
        old = c.execute(
            "SELECT id,team_name,created_at,source_created_at,source,payload,external_id,name,phone FROM leads WHERE phone=? ORDER BY id LIMIT 1", (phone,)
        ).fetchone()
        previous = c.execute(
            "SELECT id,team_name,created_at,source_created_at,source,payload,external_id,name,phone FROM leads WHERE phone=? ORDER BY id DESC LIMIT 1", (phone,)
        ).fetchone()
    if old:
        # Duplicate leads are still distributed. They are clearly marked as duplicate
        # and continue through the same round-robin queue as new leads.
        status, dup = "duplicate", old["id"]
        wa = "unknown"
        team = select_team(route_key)
    else:
        wa = await wa_check(phone)
        status, dup = ("no_whatsapp" if wa == "no" else "assigned"), None
        team = None if wa == "no" else select_team(route_key)
        if status == "assigned" and not team:
            status = "pending_no_team"
    with db() as c:
        cur = c.execute(
            """INSERT INTO leads(created_at,name,phone_raw,phone,source,status,duplicate_of,wa_status,
               team_chat_id,team_name,submitted_by,payload,source_created_at,external_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now_iso(), name, raw_phone, phone, source, status, dup, wa,
             team["chat_id"] if team else None, team["name"] if team else None,
             submitted_by, json.dumps(data, ensure_ascii=False),
             _source_created_iso(data.get("created_time")), str(data.get("external_id") or "").strip() or None),
        )
        lead_id = cur.lastrowid
    raw_text = str(data.get("raw_text") or "").strip()

    # Display-only cleanup for A/B/C staff groups.
    # The original raw_text has already been stored in the lead payload above.
    display_text = raw_text

    # Loan leads: keep original lead body for staff.
    # Anti-scam leads: use the Professional Lead Card formatter below.
    _is_loan_lead = (route_key == "loan")

    # Excel-row/manual parsers may already provide the final professional card.
    # Never parse/format that card a second time, otherwise every labelled field
    # shifts down by one (Scam Type becomes the card title, Amount becomes Scam Type, etc.).
    _already_formatted_antiscam = bool(
        display_text
        and re.match(r"^📋\s*Customer Information \(Lead Form\)\s*$", display_text.splitlines()[0].strip())
        and re.search(r"(?m)^Scam Type:\s*", display_text)
        and re.search(r"(?m)^Contact Number:\s*", display_text)
    )

    if (display_text and not _is_loan_lead and not _already_formatted_antiscam
            and _fixed_antiscam_occurrence):
        display_text = _s_manual_card(display_text, phone)
        _already_formatted_antiscam = True

    if display_text and not _is_loan_lead and not _already_formatted_antiscam:
        _lines = [x.strip() for x in display_text.splitlines() if x.strip()]
        _lines = [x for x in _lines
                  if not re.match(r"(?i)^new\s+facebook\s+lead\b", x)
                  and not re.match(r"(?i)^page\s*:", x)
                  and not re.match(r"(?i)^phone\s*:\s*$", x)]

        _email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        _phone_re = re.compile(r"^\+?[\d\s\-()]{8,20}$")
        _email = ""
        _contact = ""
        _rest = []

        for _x in _lines:
            if not _email and _email_re.match(_x):
                _email = _x
                continue
            _digits = re.sub(r"\D", "", _x)
            if (not _contact and _phone_re.match(_x)
                    and 9 <= len(_digits) <= 15
                    and (_x.startswith("+") or _x.startswith("0") or _x.startswith("6"))):
                _contact = _x
                continue
            _rest.append(_x)

        _card = ["📋 Customer Information (Lead Form)", ""]
        if len(_rest) > 0: _card.append(f"Scam Type: {_rest[0]}")
        if len(_rest) > 1: _card.append(f"Amount Lost: {_rest[1]}")
        if _email: _card.append(f"Email: {_email}")
        if len(_rest) > 2: _card.append(f"Customer Name: {_rest[2]}")
        if _contact: _card.append(f"Contact Number: {_contact}")
        for _extra in _rest[3:]:
            _card.append(_extra)

        display_text = "\n".join(_card).strip()

    if display_text and _is_loan_lead:
        _loan_lines = display_text.splitlines()
        _loan_lines = [
            x for x in _loan_lines
            if not re.match(r"(?i)^new\\s+facebook\\s+lead\\b", x.strip())
            and not re.match(r"(?i)^page\\s*:", x.strip())
            and not re.match(r"(?i)^phone\\s*:\\s*$", x.strip())
        ]
        display_text = "\n".join(_loan_lines).strip()

    msg = display_text[:3500] if display_text else f"📋 Customer Information (Lead Form)\n\nCustomer Name: {name}\nContact Number: {phone}"


    # Antiscam staff already know the receiving group; keep its card clean.
    team_footer = "" if route_key == "antiscam" else (f"\n\n🏢 {team['name']}" if team else "")

    if status == "duplicate":
        duplicate_info = {
            "first_id": old["id"],
            "first_team": old["team_name"] or "未分配",
            "first_time": old["created_at"],
        }
        with db() as c:
            duplicate_times = c.execute(
                "SELECT COUNT(*) FROM leads WHERE phone=?",
                (phone,),
            ).fetchone()[0]
        saved_status = latest_status(phone)
        labels = {"hit_before":"✅ 中过了","police":"🚔 警察","no_ws":"❌ 没WS","closed":"💰 成交"}
        status_line = f"\n{labels.get(saved_status['status'], saved_status['status'])}" if saved_status else ""
        saved_status = latest_status(phone)
        status_labels = {
            "hit_before": "✅ 中过了",
            "police": "🚔 警察",
            "no_ws": "❌ 没WS",
            "closed": "💰 成交",
        }
        status_line = ""
        if saved_status:
            status_line = f"\n🏷️ 状态：{status_labels.get(saved_status['status'], saved_status['status'])}"
        if route_key == "antiscam":
            # Sales staff only need a compact "came before" warning.
            # Keep the customer's submitted fields/card unchanged below it.
            prev_row = previous or old
            try:
                _old_code, previous_date, previous_platform = _s_row_meta(prev_row)
            except Exception:
                previous_date, previous_platform = "-", "未注明"
            current_platform = str(data.get("source_platform") or "").strip().upper()
            if current_platform not in ("FB", "TK"):
                cur_source = str(source or "").strip().casefold()
                if "tiktok" in cur_source or cur_source in ("t", "tk"):
                    current_platform = "TK"
                elif "facebook" in cur_source or cur_source in ("f", "fb"):
                    current_platform = "FB"
                else:
                    current_platform = "未注明"
            current_date = _s_display_date_from_data(data) or _lead_history_date(now_iso())
            staff_name = str(data.get("name") or name or "未填写").strip() or "未填写"
            duplicate_notice = (
                f"⚠️ 来过客户（{duplicate_times}次）\n"
                f"{staff_name}｜{phone}\n"
                f"本次：{current_platform} {current_date}\n"
                f"上次：{previous_platform} {previous_date}"
            )
        else:
            duplicate_notice = (
                f"\n\n⚠️ 重复号码（{duplicate_times}次）"
                f"\n♻️ 上次：{old['team_name'] or '未分配'}"
                f"{status_line}"
            )

        if team:
            duplicate_body = (duplicate_notice + "\n\n" + msg) if route_key == "antiscam" else (msg + duplicate_notice + team_footer)
            sent_msg = await reliable_send(
                app,
                team["chat_id"],
                duplicate_body,
            )
            if sent_msg:
                save_lead_message_map(team["chat_id"], sent_msg.message_id, lead_id, phone)
        elif notify_duplicate_owner:
            await reliable_send(
                app,
                OWNER_ID,
                msg + duplicate_notice + "\n\n⚠️ 本次未分配：还没设置团队群",
            )
    elif status == "no_whatsapp":
        await reliable_send(app, OWNER_ID, msg + "\n\n已分类：无 WhatsApp")
        duplicate_info = None
    elif team:
        sent_msg = await reliable_send(app, team["chat_id"], msg + team_footer)
        if sent_msg:
            save_lead_message_map(team["chat_id"], sent_msg.message_id, lead_id, phone)
        duplicate_info = None
    else:
        await reliable_send(app, OWNER_ID, msg + "\n\n⚠️ 尚未设置团队群")
        duplicate_info = None
    return {
        "ok": True,
        "lead_id": lead_id,
        "status": status,
        "phone": phone,
        "team": team["name"] if team else None,
        "duplicate": duplicate_info,
    }


async def lead_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update):
        return
    raw = update.effective_message.text.partition(" ")[2]
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) < 2:
        await update.effective_message.reply_text("用法：/lead 姓名 | 电话 | 来源")
        return
    try:
        result = await process_lead(
            context.application,
            {"name": parts[0], "phone": parts[1], "source": parts[2] if len(parts) > 2 else "手动"},
            update.effective_user.id,
        )
        await update.effective_message.reply_text(f"✅ 已处理 Lead #{result['lead_id']}：{result['status']}")
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")


lead_intake_queue = asyncio.Queue()
lead_worker_task = None

async def _lead_queue_worker():
    while True:
        update, application = await lead_intake_queue.get()
        try:
            await _process_inbox_message(update, application)
        except Exception:
            LOG.exception("queued inbox lead failed")
        finally:
            lead_intake_queue.task_done()

async def auto_inbox_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fast Telegram handler: enqueue every incoming Lead in FIFO order."""
    global lead_worker_task
    if lead_worker_task is None or lead_worker_task.done():
        lead_worker_task = asyncio.create_task(_lead_queue_worker())
    await lead_intake_queue.put((update, context.application))

def _lead_history_date(value):
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(TZ)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return ""

def _s_today_ad_stats(now=None):
    """Count S-inbox submissions by Bot receive time, including platform and history."""
    now = now or datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    stats = {}
    with db() as c:
        rows = c.execute(
            "SELECT status,payload FROM leads WHERE created_at>=? AND created_at<? ORDER BY id",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
            if int(payload.get("entry_chat_id") or 0) != S_MANUAL_INBOX_ID:
                continue
        except Exception:
            continue
        platform = str(payload.get("source_platform") or "未注明").strip().upper()
        if platform not in ("TK", "FB"):
            platform = "未注明"
        item = stats.setdefault(platform, {"total": 0, "new": 0, "came": 0})
        item["total"] += 1
        if row["status"] == "duplicate":
            item["came"] += 1
        else:
            item["new"] += 1
    return stats


def _s_today_ad_stats_text(now=None):
    now = now or datetime.now(TZ)
    stats = _s_today_ad_stats(now)
    total = sum(item["total"] for item in stats.values())
    new = sum(item["new"] for item in stats.values())
    came = sum(item["came"] for item in stats.values())
    lines = [f"📊 今日广告 {now.strftime('%d/%m/%Y')}：{total}"]
    for platform in ("TK", "FB", "未注明"):
        if platform in stats:
            item = stats[platform]
            lines.append(f"{platform}：{item['total']}｜新资料 {item['new']}｜来过 {item['came']}")
    lines.append(f"合计：新资料 {new}｜来过 {came}")
    return "\n".join(lines)


def _lead_payload_platform(payload, fallback_source=""):
    """Return canonical FB/TK/未注明 from a saved Lead payload/source."""
    try:
        p = str((payload or {}).get("source_platform") or "").strip().upper()
    except Exception:
        p = ""
    if p in ("FB", "TK"):
        return p
    src = str(fallback_source or "").strip().casefold()
    if "tiktok" in src or src in ("t", "tk"):
        return "TK"
    if "facebook" in src or src in ("f", "fb"):
        return "FB"
    return "未注明"


def _came_query_allowed(chat_id):
    """Allow duplicate-history reports in any configured antiscam Lead inbox."""
    try:
        cid = int(chat_id)
    except Exception:
        return False
    if cid == S_MANUAL_INBOX_ID:
        return True
    try:
        return bool(lead_inbox_is_active(cid) and inbox_route_get(cid) == "antiscam")
    except Exception:
        return False


def _payload_entry_chat_id(payload):
    try:
        return int((payload or {}).get("entry_chat_id") or 0)
    except Exception:
        return 0


def _came_row_payload(row):
    try:
        return json.loads(row["payload"] or "{}")
    except Exception:
        return {}


def _came_row_code(row, payload=None):
    payload = payload if isinstance(payload, dict) else _came_row_payload(row)
    try:
        code = str(row["external_id"] or payload.get("external_id") or "").strip()
    except Exception:
        code = str(payload.get("external_id") or "").strip()
    return code or "未注明"


def _came_row_original_time(row, payload=None):
    """Prefer the Lead's original supplied time; only fall back to bot-record time."""
    payload = payload if isinstance(payload, dict) else _came_row_payload(row)
    try:
        src = str(row["source_created_at"] or "").strip()
    except Exception:
        src = ""
    if src:
        value = _lead_history_date(src)
        if value:
            return value
    supplied = str(payload.get("created_time") or "").strip()
    if supplied:
        try:
            dt = datetime.fromisoformat(supplied.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(TZ)
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return supplied
    from_raw = _s_date_from_text(payload.get("raw_text"))
    if from_raw:
        return from_raw
    try:
        return _lead_history_date(row["created_at"]) or str(row["created_at"] or "-")
    except Exception:
        return "-"


def _came_row_name(c, row, payload, chat_id):
    """Use the name from THIS Lead occurrence; only fall back when this occurrence truly has none."""
    name = str(row["name"] or payload.get("name") or payload.get("full_name") or "").strip()
    if name and name not in ("-", "未填写", "未填寫", "未确认", "未確認", "Unconfirmed"):
        return name
    phone = str(row["phone"] or "").strip()
    if not phone:
        return "未填写"

    # Older rows may have saved name=未填写 even though the raw Lead text already
    # contained a name. Recover from that row's own raw text first, so if the
    # customer changed name between occurrences, each occurrence keeps its own name.
    try:
        raw = str(payload.get("raw_text") or "").strip()
        if raw:
            fields = _s_manual_fields(raw, phone)
            raw_name = str(fields.get("name") or "").strip()
            if raw_name and raw_name not in ("-", "未填写", "未填寫", "未确认", "未確認", "Unconfirmed"):
                return raw_name
    except Exception:
        LOG.exception("recover historical Lead occurrence name failed")
    candidates = c.execute(
        """SELECT id,name,payload FROM leads
           WHERE phone=? AND id<=? ORDER BY id DESC LIMIT 50""",
        (phone, int(row["id"])),
    ).fetchall()
    for cand in candidates:
        cp = _came_row_payload(cand)
        n = str(cand["name"] or cp.get("name") or cp.get("full_name") or "").strip()
        if n and n not in ("-", "未填写", "未填寫"):
            return n
    return "未填写"


def _s_today_came_rows(chat_id, platform=None, now=None):
    """Duplicates handled today in this Lead inbox; display original Lead dates, not send dates."""
    now = now or datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    wanted = str(platform or "").strip().upper()
    if wanted not in ("FB", "TK", "未注明"):
        wanted = ""
    cid = int(chat_id)

    with db() as c:
        rows = c.execute(
            """SELECT id,created_at,source_created_at,external_id,name,phone,source,status,payload
               FROM leads
               WHERE created_at>=? AND created_at<? AND status='duplicate'
               ORDER BY id""",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        ).fetchall()

        out = []
        for row in rows:
            payload = _came_row_payload(row)
            if _payload_entry_chat_id(payload) != cid:
                continue

            current_platform = _lead_payload_platform(payload, row["source"])
            if wanted and current_platform != wanted:
                continue

            current_code = _came_row_code(row, payload)
            current_time = _came_row_original_time(row, payload)
            name = _came_row_name(c, row, payload, cid)

            # Find the previous genuine occurrence in the SAME inbox. Skip the same Code,
            # because a repeated copy of the same Code is not a "来过" occurrence.
            previous = None
            previous_payload = {}
            candidates = c.execute(
                """SELECT id,created_at,source_created_at,external_id,name,phone,source,payload
                   FROM leads WHERE phone=? AND id<? ORDER BY id DESC LIMIT 100""",
                (row["phone"], row["id"]),
            ).fetchall()
            for cand in candidates:
                cp = _came_row_payload(cand)
                cand_code = _came_row_code(cand, cp)
                if current_code != "未注明" and cand_code == current_code:
                    continue
                previous = cand
                previous_payload = cp
                break

            previous_platform = "未注明"
            previous_time = "-"
            previous_code = "未注明"
            if previous:
                previous_platform = _lead_payload_platform(previous_payload, previous["source"])
                previous_time = _came_row_original_time(previous, previous_payload)
                previous_code = _came_row_code(previous, previous_payload)

            # Count genuine occurrences in this inbox. Same-Code copies count once.
            hist = c.execute(
                """SELECT id,external_id,payload FROM leads
                   WHERE phone=? AND id<=? ORDER BY id""",
                (row["phone"], row["id"]),
            ).fetchall()
            seen = set()
            for h in hist:
                hp = _came_row_payload(h)
                hc = _came_row_code(h, hp)
                key = ("code", hc) if hc != "未注明" else ("row", int(h["id"]))
                seen.add(key)

            out.append({
                "id": row["id"],
                "name": name,
                "phone": str(row["phone"] or "").strip(),
                "current_code": current_code,
                "current_platform": current_platform,
                "current_time": current_time,
                "previous_code": previous_code,
                "previous_platform": previous_platform,
                "previous_time": previous_time,
                "times": max(1, len(seen)),
            })
    return out


def _came_short_date(value):
    text = str(value or "").strip()
    if not text or text == "-":
        return "-"
    m = re.match(r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    return m.group(1).replace("-", "/") if m else text


def _s_today_came_text(chat_id, platform=None, now=None, full=False):
    now = now or datetime.now(TZ)
    rows = _s_today_came_rows(chat_id, platform, now)
    label = str(platform or "全部").strip().upper()
    if label not in ("FB", "TK", "未注明"):
        label = "全部"
    mode = "完整版" if full else "简易版"
    lines = [f"📋 今日来过号码 {now.strftime('%d/%m/%Y')}｜{label}：{len(rows)}｜{mode}"]
    if not rows:
        lines.append("今天没有符合条件的来过号码。")
        return "\n".join(lines)

    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['phone']}｜{r['name']}")
        if full:
            lines.append(f"本次 {r['current_code']}｜{r['current_time']}｜{r['current_platform']}")
            lines.append(f"上次 {r['previous_code']}｜{r['previous_time']}｜{r['previous_platform']}")
        else:
            lines.append(f"本次：{r['current_platform']} {_came_short_date(r['current_time'])}")
            lines.append(f"上次：{r['previous_platform']} {_came_short_date(r['previous_time'])}")
        lines.append(f"次数：{r['times']}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


async def _reply_long_text(msg, text, limit=3900):
    """Reply in Telegram-safe chunks while keeping one logical list."""
    if len(text) <= limit:
        await msg.reply_text(text)
        return
    chunk = []
    size = 0
    for line in text.splitlines():
        add = len(line) + 1
        if chunk and size + add > limit:
            await msg.reply_text("\n".join(chunk))
            chunk = []
            size = 0
        chunk.append(line)
        size += add
    if chunk:
        await msg.reply_text("\n".join(chunk))


def _s_parse_came_query(text):
    """Recognize 来过 queries, with or without a leading slash."""
    x = re.sub(r"\s+", " ", str(text or "").strip()).casefold()
    # Users often type /来过 in Telegram. Treat it exactly like 来过.
    x = re.sub(r"^/+", "", x).strip()
    if not x:
        return None
    if x in ("来过", "來過"):
        return ""
    patterns = (
        (r"^(?:来过|來過)\s*(?:fb|facebook)$", "FB"),
        (r"^(?:fb|facebook)\s*(?:来过|來過)$", "FB"),
        (r"^(?:来过|來過)\s*(?:tk|tiktok)$", "TK"),
        (r"^(?:tk|tiktok)\s*(?:来过|來過)$", "TK"),
        (r"^(?:来过|來過)\s*未注明$", "未注明"),
        (r"^未注明\s*(?:来过|來過)$", "未注明"),
    )
    for pattern, value in patterns:
        if re.match(pattern, x, re.I):
            return value
    return None

def _s_source_label_from_data(data):
    p = str((data or {}).get("source_platform") or "").strip().upper()
    if p in ("FB", "TK"):
        return p
    src = str((data or {}).get("source") or "").strip().casefold()
    if "tiktok" in src or src in ("t", "tk"):
        return "TK"
    if "facebook" in src or src in ("f", "fb"):
        return "FB"
    return "未注明"


def _s_date_from_text(text):
    raw = str(text or "")
    # Keep the actual supplied Lead date/time when one exists.
    m = re.search(r"(?i)(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{4})(?:[ T]+(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*(AM|PM))?)?", raw)
    if m:
        value = m.group(1)
        if m.group(2):
            value += " " + m.group(2)
            if m.group(3):
                value += " " + m.group(3).upper()
        return value
    m = re.search(r"(?i)(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{2})(?:[ T]+(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*(AM|PM))?)?", raw)
    if m:
        value = m.group(1)
        if m.group(2):
            value += " " + m.group(2)
            if m.group(3):
                value += " " + m.group(3).upper()
        return value
    return ""


def _s_display_date_from_data(data):
    supplied = str((data or {}).get("created_time") or "").strip()
    if supplied:
        try:
            dt = datetime.fromisoformat(supplied.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = dt.astimezone(TZ)
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            return supplied
    from_raw = _s_date_from_text((data or {}).get("raw_text"))
    return from_raw or datetime.now(TZ).strftime("%d/%m/%Y %H:%M:%S")


def _s_row_meta(row):
    payload = {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        pass
    code = str(row["external_id"] or payload.get("external_id") or "未注明").strip() or "未注明"
    source = _s_source_label_from_data(payload)
    date = ""
    try:
        if row["source_created_at"]:
            date = _lead_history_date(row["source_created_at"])
    except Exception:
        pass
    if not date:
        date = _s_display_date_from_data(payload)
    if not date:
        date = _lead_history_date(row["created_at"]) or str(row["created_at"] or "-")
    return code, date, source


def _s_duplicate_precheck(data):
    """Return (kind, previous_row) for S manual intake before anything is inserted/sent."""
    phone = normalize_phone(str(data.get("phone") or ""))
    code = str(data.get("external_id") or "").strip()
    with db() as c:
        if code:
            row = c.execute(
                """SELECT id,created_at,source_created_at,source,payload,external_id,phone,team_chat_id,team_name
                   FROM leads WHERE external_id=? ORDER BY id DESC LIMIT 1""",
                (code,),
            ).fetchone()
            if row:
                return "same_code", row
        row = c.execute(
            """SELECT id,created_at,source_created_at,source,payload,external_id,phone,team_chat_id,team_name
               FROM leads WHERE phone=? ORDER BY id DESC LIMIT 1""",
            (phone,),
        ).fetchone()
        if row:
            return "came", row
    return None, None


def _s_pending_store(chat_id, user_id, kind, old_lead_id, data):
    with db() as c:
        cur = c.execute(
            "INSERT INTO lead_resend_pending(chat_id,requested_by,kind,old_lead_id,data_json,created_at) VALUES(?,?,?,?,?,?)",
            (int(chat_id), int(user_id), str(kind), int(old_lead_id) if old_lead_id else None,
             json.dumps(data, ensure_ascii=False), now_iso()),
        )
        return int(cur.lastrowid)


def _s_pending_markup(pid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认发送", callback_data=f"ldconfirm:{int(pid)}"),
        InlineKeyboardButton("❌ 取消", callback_data=f"ldcancel:{int(pid)}"),
    ]])


def _s_duplicate_confirm_text(kind, previous_row, data):
    old_code, old_date, old_source = _s_row_meta(previous_row)
    new_code = str(data.get("external_id") or "未注明").strip() or "未注明"
    new_date = _s_display_date_from_data(data)
    new_source = _s_source_label_from_data(data)
    phone = normalize_phone(str(data.get("phone") or ""))
    if kind == "same_code":
        return (
            "⚠️ 检测到重复 copy（同 Code）\n\n"
            f"📞 电话：{phone}\n"
            f"上次 Code：{old_code}\n"
            f"上次日期：{old_date}\n"
            f"上次来源：{old_source}\n"
            f"本次 Code：{new_code}\n"
            f"本次日期：{new_date}\n"
            f"本次来源：{new_source}\n\n"
            "同 Code 不会计算为「来过」。\n是否确认再次发送？"
        )
    return (
        "⚠️ 这个号码以前来过\n\n"
        f"📞 电话：{phone}\n"
        f"上次 Code：{old_code}\n"
        f"上次日期：{old_date}\n"
        f"上次来源：{old_source}\n"
        f"本次 Code：{new_code}\n"
        f"本次日期：{new_date}\n"
        f"本次来源：{new_source}\n\n"
        "是否确认再次发送？"
    )


async def _s_send_same_code_confirmed(app, pending_row, data):
    """Resend a same-Code Lead without inserting another leads row or counting it as 来过."""
    route_key = str(data.get("route_key") or "antiscam").strip().casefold()
    team = select_team(route_key)
    if not team:
        return False, "还没设置团队群"
    phone = normalize_phone(str(data.get("phone") or ""))
    raw = str(data.get("raw_text") or "").strip()
    body = _s_manual_card(raw, phone) if raw else f"📋 Customer Information (Lead Form)\n\nContact Number: {phone}"
    body += "\n\n⚠️ 同 Code 重复 copy｜已确认再次发送｜不计来过"
    sent = await reliable_send(app, team["chat_id"], body[:4096])
    if sent:
        old_id = int(pending_row["old_lead_id"] or 0)
        if old_id:
            save_lead_message_map(team["chat_id"], sent.message_id, old_id, phone)
        return True, team["name"]
    return False, "发送失败"


async def lead_duplicate_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        action, raw_id = (q.data or "").split(":", 1)
        pid = int(raw_id)
    except Exception:
        try:
            await q.answer("按钮资料无效，请重新贴 Lead。", show_alert=True)
        except Exception:
            pass
        return

    with db() as c:
        row = c.execute("SELECT * FROM lead_resend_pending WHERE id=?", (pid,)).fetchone()

    if not row:
        try:
            await q.answer("这次确认已经失效，请重新贴 Lead。", show_alert=True)
        except Exception:
            pass
        return

    uid = int(update.effective_user.id) if update.effective_user else 0
    allowed = {int(row["requested_by"])}
    if OWNER_ID:
        allowed.add(int(OWNER_ID))
    if uid not in allowed:
        try:
            await q.answer("只有提交这条 Lead 的人可以确认。", show_alert=True)
        except Exception:
            pass
        return

    # Answer exactly once so Telegram Web/Desktop does not leave the button spinning.
    try:
        await q.answer("处理中…")
    except Exception:
        pass

    if action == "ldcancel":
        with db() as c:
            c.execute("DELETE FROM lead_resend_pending WHERE id=?", (pid,))
        result_text = "❌ 已取消，不发送。"
    else:
        try:
            data = json.loads(row["data_json"] or "{}")
        except Exception:
            data = {}
        try:
            if row["kind"] == "same_code":
                ok, info = await _s_send_same_code_confirmed(context.application, row, data)
                result_text = f"✅ 已确认再次发送 → {info}\n同 Code：不计入「来过」。" if ok else f"⚠️ 未发送：{info}"
            else:
                result = await process_lead(
                    context.application, data,
                    submitted_by=int(row["requested_by"]), notify_duplicate_owner=False,
                )
                if result.get("team"):
                    result_text = f"✅ 已确认发送｜来过 → {result['team']}"
                else:
                    result_text = "⚠️ 已确认，但还没设置团队群。"
        except Exception:
            LOG.exception("lead duplicate confirm callback failed pid=%s", pid)
            result_text = "⚠️ 确认失败，请重新贴这条 Lead 再试。"
        else:
            with db() as c:
                c.execute("DELETE FROM lead_resend_pending WHERE id=?", (pid,))

    # Prefer replacing the confirmation card; if Telegram refuses, send a fallback reply
    # so a successful click can never look like 'nothing happened'.
    try:
        await q.edit_message_text(result_text)
    except Exception:
        LOG.exception("lead duplicate confirm edit failed pid=%s", pid)
        try:
            if q.message:
                await q.message.reply_text(result_text)
        except Exception:
            LOG.exception("lead duplicate confirm fallback reply failed pid=%s", pid)


async def _process_inbox_message(update: Update, application: Application):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    # IMPORTANT: all human members in the configured Lead inbox may submit Leads.
    # No OWNER/staff/admin/user-id restriction here.
    if chat.type == "private":
        return
    text = msg.text or msg.caption or ""
    smart_schema = bool(
        _s_schema_text(text) is not None
        and _s_unwrap_platform(text)[1] in ("FB", "TK")
    )

    route_key = inbox_route_get(chat.id)
    if not route_key:
        # A complete marked FB/TK row is self-identifying as an antiscam Lead.
        # This keeps future Lead groups working without hard-coding every chat ID.
        if smart_schema and int(chat.id) not in {
            -1003941232666, -1004348567725, -1004482054615, -1004444989940
        }:
            route_key = "antiscam"
        else:
            # Legacy migration only for previously configured inboxes.
            if not lead_inbox_is_active(chat.id):
                return
            route_key = inbox_route_get(chat.id) or route_key_from_title(chat.title or "")
    if not user or user.is_bot:
        return
    submitter_name = user.full_name or user.username or str(user.id)
    with db() as c:
        c.execute(
            """INSERT INTO submitters(user_id,name,updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 name=excluded.name, updated_at=excluded.updated_at""",
            (user.id, submitter_name, now_iso()),
        )

    scoped_manual = int(chat.id) == S_MANUAL_INBOX_ID or smart_schema
    phones = _s_lead_phones(text) if scoped_manual else extract_phones(text)
    if not phones:
        return

    name = guess_name(text)
    manual_fields = None
    if scoped_manual and len(phones) == 1:
        manual_fields = _s_manual_fields(text, phones[0][1])
        name = manual_fields.get("name", name)
    results = []
    for raw_phone, _normalized in phones:
        try:
            if scoped_manual:
                fields = manual_fields if (manual_fields is not None and len(phones) == 1) else _s_manual_fields(text, _normalized)
                scoped_extra = {
                    "source_platform": fields.get("platform", "未注明"),
                    # Code/编号 is deliberately stored as external_id so the same
                    # pasted record can be ignored without affecting phone history.
                    "external_id": str(fields.get("reference") or "").strip(),
                    # Keep the Lead's original/source time separate from Telegram send time.
                    "created_time": str(fields.get("date") or "").strip(),
                    # Persist the exact six-column values from THIS occurrence so staff
                    # cards, history and duplicate checks do not re-guess them later.
                    "scam_type": str(fields.get("scam_type") or "").strip(),
                    "amount_lost": str(fields.get("amount") or "").strip(),
                }
            else:
                scoped_extra = {}
            lead_data = {
                "name": name,
                "phone": _normalized if scoped_manual else raw_phone,
                "source": chat.title or "Telegram入口群",
                "route_key": route_key,
                "entry_chat_id": chat.id,
                "raw_text": text,
                **scoped_extra,
            }
            if scoped_manual:
                kind, previous_row = _s_duplicate_precheck(lead_data)
                if kind and previous_row:
                    pid = _s_pending_store(chat.id, user.id, kind, previous_row["id"], lead_data)
                    await msg.reply_text(
                        _s_duplicate_confirm_text(kind, previous_row, lead_data),
                        reply_markup=_s_pending_markup(pid),
                    )
                    return
            result = await process_lead(
                application, lead_data,
                submitted_by=user.id,
                notify_duplicate_owner=False,
            )
            results.append(result)
        except Exception:
            LOG.exception("auto inbox lead failed")
            results.append({
                "ok": False,
                "status": "send_failed",
                "phone": _normalized,
                "team": None,
                "duplicate": None,
            })

    if not results:
        await msg.reply_text("❌ 号码读取失败，请检查格式。")
        return

    lines = []
    for r in results:
        if r["status"] == "assigned":
            lines.append(f"✅ {r['phone']} → {r['team']}")
        elif r["status"] == "same_code":
            code_text = str(r.get("external_id") or "").strip()
            lines.append(
                f"⏭️ 同 Code 已处理：{code_text or '未注明'}｜{r['phone']}｜不重复分配、不计来过"
            )
        elif r["status"] == "duplicate":
            d = r["duplicate"] or {}
            if int(chat.id) == S_MANUAL_INBOX_ID:
                previous_date = _lead_history_date(d.get("first_time"))
                previous = f"｜上次：{previous_date}" if previous_date else ""
                if r.get("team"):
                    lines.append(f"⚠️ 来过｜{r['phone']}{previous} → {r['team']}")
                else:
                    lines.append(f"⚠️ 来过｜{r['phone']}{previous}｜未分配：还没设置团队群")
            elif r.get("team"):
                lines.append(
                    f"♻️ {r['phone']} 重复｜仍已分配 → {r['team']}｜首次 #{d.get('first_id')}｜上次：{d.get('first_team')}"
                )
            else:
                lines.append(
                    f"♻️ {r['phone']} 重复｜未分配：还没设置团队群｜首次 #{d.get('first_id')}"
                )
        elif r["status"] == "no_whatsapp":
            lines.append(f"❌ {r['phone']} 无 WhatsApp，不分配")
        elif r["status"] == "send_failed":
            lines.append(f"🔴 {r['phone']} 发送失败")
        else:
            lines.append(f"⚠️ {r['phone']} 未分配：还没设置团队群")
    response = "✅ 已处理\n" + "\n".join(lines)
    if int(chat.id) == S_MANUAL_INBOX_ID:
        response += "\n\n" + _s_today_ad_stats_text()
    await msg.reply_text(response)


def _report_bounds(day):
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return start, start + timedelta(days=1)


def _report_stats(start, end):
    """Stats by original Lead time when available, otherwise bot receive time."""
    start_iso, end_iso = start.isoformat(), end.isoformat()
    time_expr = "COALESCE(source_created_at, created_at)"
    with db() as c:
        raw_total = c.execute(
            f"SELECT COUNT(*) n FROM leads WHERE {time_expr}>=? AND {time_expr}<?",
            (start_iso, end_iso),
        ).fetchone()["n"]
        total = c.execute(
            f"SELECT COUNT(DISTINCT phone) n FROM leads WHERE {time_expr}>=? AND {time_expr}<?",
            (start_iso, end_iso),
        ).fetchone()["n"]
        duplicate = max(0, raw_total - total)
        first_cte = f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY phone ORDER BY {time_expr} ASC, id ASC
                ) AS rn
                FROM leads
                WHERE {time_expr}>=? AND {time_expr}<?
            )
        """
        assigned = c.execute(
            first_cte + "SELECT COUNT(*) n FROM ranked WHERE rn=1 AND team_chat_id IS NOT NULL",
            (start_iso, end_iso),
        ).fetchone()["n"]
        team_rows = c.execute(
            first_cte + """SELECT COALESCE(team_name,'未分配') team_name, COUNT(*) n
                           FROM ranked WHERE rn=1 AND team_chat_id IS NOT NULL
                           GROUP BY team_name ORDER BY n DESC""",
            (start_iso, end_iso),
        ).fetchall()
        seller_rows = c.execute(
            first_cte + """SELECT submitted_by, COUNT(*) n FROM ranked WHERE rn=1
                           GROUP BY submitted_by ORDER BY n DESC""",
            (start_iso, end_iso),
        ).fetchall()
        status_rows = c.execute(
            f"""SELECT ls.status, COUNT(DISTINCT l.phone) n
                FROM leads l JOIN lead_status ls ON ls.phone=l.phone
                WHERE {time_expr}>=? AND {time_expr}<? GROUP BY ls.status""",
            (start_iso, end_iso),
        ).fetchall()
        sellers=[]
        for row in seller_rows:
            uid=row["submitted_by"]
            if uid is None or uid == 0:
                name="Auto/Import"
            else:
                nr=c.execute("SELECT name FROM submitters WHERE user_id=?",(uid,)).fetchone()
                if not nr:
                    nr=c.execute("SELECT name FROM assistants WHERE user_id=?",(uid,)).fetchone()
                name=nr["name"] if nr and nr["name"] else str(uid)
            sellers.append((name,row["n"]))
    return {
        "raw":raw_total,"total":total,"duplicate":duplicate,"assigned":assigned,
        "unassigned":max(0,total-assigned),"teams":[(r["team_name"],r["n"]) for r in team_rows],
        "sellers":sellers,"statuses":{r["status"]:r["n"] for r in status_rows},
    }


def report_text(target_day=None):
    day = target_day or datetime.now(TZ).date()
    start,end=_report_bounds(day)
    x=_report_stats(start,end)
    team_lines="\n".join(f"• {n}：{v}" for n,v in x["teams"]) or "• 暂无"
    seller_lines="\n".join(f"• {n}：{v}" for n,v in x["sellers"]) or "• 暂无"
    st=x["statuses"]
    return (
        f"📊 Lead Bot 日报\n🇲🇾 {day:%Y-%m-%d} · Malaysia Time\n\n"
        f"📥 Lead：{x['total']}\n🏢 已分配：{x['assigned']}\n♻️ 重复：{x['duplicate']}\n"
        f"⚠️ 未分配：{x['unassigned']}\n🧾 原始记录：{x['raw']}\n\n"
        f"🏷️ Lead 状态\n✅ 中过了：{st.get('hit_before',0)}\n🚔 警察：{st.get('police',0)}\n"
        f"❌ 没WS：{st.get('no_ws',0)}\n💰 成交：{st.get('closed',0)}\n\n"
        f"👥 业务员提交\n{seller_lines}\n\n🏢 团队分配\n{team_lines}"
    )


def _parse_day_arg(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update): return
    try:
        day = _parse_day_arg(context.args[0]) if context.args else datetime.now(TZ).date()
        await update.effective_message.reply_text(report_text(day))
    except ValueError:
        await update.effective_message.reply_text("格式：/report 2026-08-31")
    except Exception as e:
        LOG.exception("manual report failed")
        await update.effective_message.reply_text(f"⚠️ 报表读取失败 / Report error\n{type(e).__name__}: {e}")


def _period_text(kind, anchor):
    if kind == "week":
        start_day = anchor - timedelta(days=anchor.weekday())
        end_day = start_day + timedelta(days=7)
        title=f"周报 · {start_day:%Y-%m-%d} → {(end_day-timedelta(days=1)):%Y-%m-%d}"
    else:
        start_day = anchor.replace(day=1)
        if start_day.month == 12: end_day = start_day.replace(year=start_day.year+1,month=1)
        else: end_day = start_day.replace(month=start_day.month+1)
        title=f"月报 · {start_day:%Y-%m}"
    lines=[]; d=start_day
    while d < end_day:
        a,b=_report_bounds(d); x=_report_stats(a,b)
        lines.append(f"{d:%m-%d}｜Lead {x['total']}｜重复 {x['duplicate']}｜已分配 {x['assigned']}｜未分配 {x['unassigned']}")
        d += timedelta(days=1)
    a=datetime(start_day.year,start_day.month,start_day.day,tzinfo=TZ)
    b=datetime(end_day.year,end_day.month,end_day.day,tzinfo=TZ)
    t=_report_stats(a,b)
    return (f"📊 Lead Bot {title}\n🇲🇾 Malaysia Time\n\n" + "\n".join(lines) +
            f"\n\n🔢 TOTAL\n📥 Lead：{t['total']}\n♻️ 重复：{t['duplicate']}\n🏢 已分配：{t['assigned']}\n⚠️ 未分配：{t['unassigned']}\n🧾 原始记录：{t['raw']}")


async def week_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update): return
    try:
        anchor=_parse_day_arg(context.args[0]) if context.args else datetime.now(TZ).date()
        await update.effective_message.reply_text(_period_text("week",anchor))
    except ValueError:
        await update.effective_message.reply_text("格式：/week 2026-08-31")


async def month_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require(update): return
    try:
        if context.args:
            anchor=datetime.strptime(context.args[0],"%Y-%m").date()
        else:
            anchor=datetime.now(TZ).date().replace(day=1)
        await update.effective_message.reply_text(_period_text("month",anchor))
    except ValueError:
        await update.effective_message.reply_text("格式：/month 2026-08")


async def daily_report(app):
    """Send exactly once per Malaysia day at 23:59."""
    sent_for_date=None
    while True:
        now=datetime.now(TZ)
        if now.hour==23 and now.minute==59 and sent_for_date!=now.date():
            try:
                await reliable_send(app,OWNER_ID,report_text(now.date()))
                sent_for_date=now.date()
                LOG.info("23:59 Malaysia daily report sent")
            except Exception: LOG.exception("daily report failed")
        await asyncio.sleep(20)


def excel_sync_init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS excel_processed_leads(
          source_id TEXT PRIMARY KEY,
          processed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS excel_sync_state(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """)


def _excel_download_candidates(url: str):
    """Build public-download candidates for a OneDrive share link.

    Personal OneDrive short links are inconsistent: some accept download=1 directly,
    while others work through the public shares/content endpoint. Try both.
    """
    if not url:
        return []
    url = url.strip()
    base = url.split("?", 1)[0]
    encoded_full = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    encoded_base = base64.urlsafe_b64encode(base.encode("utf-8")).decode("ascii").rstrip("=")
    return [
        base + "?download=1",
        url + ("&download=1" if "?" in url else "?download=1"),
        f"https://api.onedrive.com/v1.0/shares/u!{encoded_full}/root/content",
        f"https://api.onedrive.com/v1.0/shares/u!{encoded_base}/root/content",
        url,
    ]


EXCEL_SYNC_RUNTIME = {
    "last_ok": None,
    "last_error": None,
    "last_rows": 0,
    "last_lead_rows": 0,
    "last_imported": 0,
    "last_url": None,
}


async def _download_excel_bytes():
    last_error = None
    headers = {"User-Agent": "Mozilla/5.0 LeadBot/2.0", "Accept": "*/*"}
    async with ClientSession(headers=headers) as session:
        for url in _excel_download_candidates(EXCEL_SOURCE_URL):
            try:
                async with session.get(url, allow_redirects=True, timeout=30) as resp:
                    body = await resp.read()
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    # xlsx is a ZIP container and starts PK.
                    if resp.status == 200 and body[:2] == b"PK":
                        EXCEL_SYNC_RUNTIME["last_url"] = str(resp.url)
                        return body
                    last_error = RuntimeError(
                        f"HTTP {resp.status}, type={ctype or '-'}, bytes={len(body)}, final={resp.url}"
                    )
            except Exception as e:
                last_error = e
    raise last_error or RuntimeError("Unable to download OneDrive Excel")


def _lead_status_value(row: dict, raw_values=None, keys=None):
    # Prefer the named column; this keeps working even if Microsoft moves the column.
    for k, v in row.items():
        if str(k).strip().lower() == "lead_status":
            return str(v or "").strip()
    # User's current online workbook uses column S for the Lead gate in some layouts.
    if raw_values and len(raw_values) >= 19:
        return str(raw_values[18] or "").strip()
    return ""


def _is_ready_lead(row: dict) -> bool:
    return str(row.get("__lead_status__") or row.get("lead_status") or "").strip().lower() == "lead"


def _rows_from_antiscam_workbook(blob: bytes):
    wb = load_workbook(BytesIO(blob), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    headers = next(rows, None)
    if not headers:
        return []
    keys = [str(x).strip() if x is not None else "" for x in headers]
    out = []
    for values in rows:
        row = {keys[i]: values[i] for i in range(min(len(keys), len(values))) if keys[i]}
        source_id = str(row.get("id") or "").strip()
        if not source_id:
            continue
        row["__lead_status__"] = _lead_status_value(row, values, keys)
        out.append(row)
    return out


def _excel_seen(source_id: str) -> bool:
    with db() as c:
        return c.execute(
            "SELECT 1 FROM excel_processed_leads WHERE source_id=?", (source_id,)
        ).fetchone() is not None


def _excel_mark_seen(source_id: str):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO excel_processed_leads(source_id,processed_at) VALUES(?,?)",
            (source_id, now_iso()),
        )


def _excel_is_seeded() -> bool:
    with db() as c:
        return c.execute(
            "SELECT 1 FROM excel_sync_state WHERE key='seeded'"
        ).fetchone() is not None


def _excel_set_seeded():
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO excel_sync_state(key,value) VALUES('seeded',?)", (now_iso(),)
        )


def _excel_scam_type(row: dict) -> str:
    """Find Scam Type even when Facebook/Excel changes the exported column label."""
    aliases = {
        "bagaimanakah_anda_telah_ditipu",
        "bagaimanakah_anda_telah_ditipu?",
        "bagaimanakah anda telah ditipu",
        "bagaimanakah anda telah ditipu?",
        "bagaimana anda ditipu",
        "scam type",
        "scam_type",
        "jenis scam",
        "jenis penipuan",
        "type of scam",
    }

    def norm(v):
        x = str(v or "").strip().lower()
        x = re.sub(r"[_\-]+", " ", x)
        x = re.sub(r"[^a-z0-9? ]+", " ", x)
        return re.sub(r"\s+", " ", x).strip()

    normalized_aliases = {norm(x).rstrip("?") for x in aliases}
    for k, v in row.items():
        nk = norm(k).rstrip("?")
        if nk in normalized_aliases and str(v or "").strip():
            return str(v).strip()

    for k, v in row.items():
        nk = norm(k).rstrip("?")
        if str(v or "").strip() and (
            "bagaimana anda ditipu" in nk
            or "bagaimanakah anda telah ditipu" in nk
            or "scam type" in nk
            or "jenis scam" in nk
            or "jenis penipuan" in nk
            or "type of scam" in nk
        ):
            return str(v).strip()
    return "-"


def _excel_get_by_alias(row: dict, aliases, default=""):
    """Return a value from Excel/Facebook rows using tolerant header matching."""
    def norm(v):
        x = str(v or "").strip().lower()
        x = re.sub(r"[_\-]+", " ", x)
        x = re.sub(r"[^a-z0-9 ]+", " ", x)
        return re.sub(r"\s+", " ", x).strip()

    wanted = {norm(a) for a in aliases}
    for k, v in row.items():
        if norm(k) in wanted and str(v or "").strip():
            return str(v).strip()
    return default


def _excel_to_lead(row: dict):
    # Current Excel layout:
    # Code | Nama | Nombor WhatsApp | Jumlah wang yang | Bagaimana anda ditipu | Date
    scam_type = _excel_get_by_alias(row, [
        "Bagaimana anda ditipu", "Bagaimanakah anda telah ditipu",
        "Scam Type", "Jenis Scam", "Jenis Penipuan"
    ], _excel_scam_type(row)) or "-"
    amount = _excel_get_by_alias(row, [
        "Jumlah wang yang", "Jumlah wang", "Berapakah jumlah kerugian anda",
        "Amount Lost", "Amount"
    ], "-") or "-"
    email = _excel_get_by_alias(row, ["Email", "E-mail"], "-") or "-"
    name = _excel_get_by_alias(row, [
        "Nama", "Full Name", "full_name", "Customer Name", "Name"
    ], "-") or "-"
    phone = _excel_get_by_alias(row, [
        "Nombor WhatsApp", "Nombor Whatsapp", "WhatsApp", "Whatsapp",
        "Contact Number", "Phone", "Phone Number", "whatsapp_电话号码"
    ], "")
    if phone.endswith(".0"):
        phone = phone[:-2]
    card = (
        "📋 Customer Information (Lead Form)\n"
        f"Scam Type: {scam_type}\n"
        f"Amount Lost: {amount}\n"
        f"Email: {email}\n"
        f"Customer Name: {name}\n"
        f"Contact Number: {phone}"
    )
    return {
        "phone": phone,
        "name": name,
        "full_name": name,
        "source": "OneDrive Excel Antiscam",
        "route_key": "antiscam",
        "raw_text": card,
        "external_id": str(row.get("id") or ""),
        "lead_status": row.get("lead_status"),
        "created_time": row.get("created_time"),
        "scam_type": scam_type,
        "amount_lost": amount,
        "email": email,
    }


async def excel_sync_worker(app: Application):
    """Check OneDrive every few seconds; ONLY rows whose lead_status is Lead can enter the bot."""
    excel_sync_init()
    while True:
        try:
            blob = await _download_excel_bytes()
            rows = _rows_from_antiscam_workbook(blob)
            ready_rows = [r for r in rows if _is_ready_lead(r)]
            EXCEL_SYNC_RUNTIME.update({
                "last_ok": now_iso(), "last_error": None,
                "last_rows": len(rows), "last_lead_rows": len(ready_rows), "last_imported": 0,
            })

            if not _excel_is_seeded() and not EXCEL_IMPORT_EXISTING:
                # Baseline only already-ready Lead rows. Non-Lead rows stay unconsumed so
                # they can enter later when their status becomes Lead.
                for row in ready_rows:
                    _excel_mark_seen(str(row.get("id") or "").strip())
                _excel_set_seeded()
                LOG.info("Excel sync seeded %s existing ready Lead rows", len(ready_rows))
            else:
                if not _excel_is_seeded():
                    _excel_set_seeded()
                imported = 0
                for row in rows:
                    source_id = str(row.get("id") or "").strip()
                    if not source_id or _excel_seen(source_id):
                        continue
                    # Important: don't consume CREATED/blank/test statuses.
                    if not _is_ready_lead(row):
                        continue
                    try:
                        await process_lead(app, _excel_to_lead(row), submitted_by=0)
                        _excel_mark_seen(source_id)
                        imported += 1
                    except ValueError as e:
                        # Do NOT mark seen. If the phone is corrected later, this same ID
                        # must still be allowed to enter.
                        LOG.warning("Excel Lead %s waiting for valid data: %s", source_id, e)
                    except Exception:
                        LOG.exception("Excel Lead %s import failed", source_id)
                EXCEL_SYNC_RUNTIME["last_imported"] = imported
                if imported:
                    LOG.info("Excel sync imported %s new antiscam Lead rows", imported)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            EXCEL_SYNC_RUNTIME["last_error"] = f"{type(e).__name__}: {e}"
            LOG.exception("Excel sync cycle failed")
        await asyncio.sleep(EXCEL_POLL_SECONDS)




def _decode_csv_bytes(blob: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            pass
    return blob.decode("utf-8", errors="replace")


def _rows_from_antiscam_csv(blob: bytes):
    text = _decode_csv_bytes(blob)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\\t")
    except Exception:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    all_rows = list(reader)
    if not all_rows:
        return []
    headers = [str(x or "").strip() for x in all_rows[0]]
    out = []
    for values in all_rows[1:]:
        row = {headers[i]: values[i] for i in range(min(len(headers), len(values))) if headers[i]}
        source_id = str(row.get("id") or "").strip()
        if not source_id:
            continue
        row["__lead_status__"] = _lead_status_value(row, values, headers)
        out.append(row)
    return out


async def csv_import_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner can drop an exported anti-scam CSV into Telegram; only Lead rows enter A/B/C."""
    msg = update.effective_message
    user = update.effective_user
    doc = getattr(msg, "document", None)
    if not doc or not user or int(user.id) != int(OWNER_ID):
        return
    filename = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    if not (filename.endswith(".csv") or "csv" in mime):
        return
    try:
        tgfile = await context.bot.get_file(doc.file_id)
        blob = bytes(await tgfile.download_as_bytearray())
        rows = _rows_from_antiscam_csv(blob)
        ready = [r for r in rows if _is_ready_lead(r)]
        imported = skipped = waiting = 0
        for row in ready:
            source_id = str(row.get("id") or "").strip()
            if not source_id or _excel_seen(source_id):
                skipped += 1
                continue
            try:
                await process_lead(context.application, _excel_to_lead(row), submitted_by=user.id)
                _excel_mark_seen(source_id)
                imported += 1
            except ValueError:
                waiting += 1
            except Exception:
                LOG.exception("CSV Lead %s import failed", source_id)
                waiting += 1
        await msg.reply_text(
            "✅ CSV Lead 导入完成\n"
            f"状态=Lead：{len(ready)}\n"
            f"新发送：{imported}\n"
            f"已处理跳过：{skipped}\n"
            f"资料/号码待修正：{waiting}\n\n"
            "手动 Copy Lead 功能保持不变。"
        )
    except Exception as e:
        LOG.exception("CSV import failed")
        await msg.reply_text(f"❌ CSV 导入失败：{type(e).__name__}: {e}")
    raise ApplicationHandlerStop


async def excel_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    err = EXCEL_SYNC_RUNTIME.get("last_error")
    ok = EXCEL_SYNC_RUNTIME.get("last_ok")
    await update.effective_message.reply_text(
        "📗 OneDrive Excel 自动入口\n"
        f"检查间隔：{EXCEL_POLL_SECONDS} 秒\n"
        f"最后成功：{ok or '-'}\n"
        f"读取行数：{EXCEL_SYNC_RUNTIME.get('last_rows', 0)}\n"
        f"状态=Lead：{EXCEL_SYNC_RUNTIME.get('last_lead_rows', 0)}\n"
        f"本轮新发：{EXCEL_SYNC_RUNTIME.get('last_imported', 0)}\n"
        f"连接：{'✅' if ok and not err else '❌'}"
        + (f"\n错误：{err[:700]}" if err else "")
    )


async def http_lead(request):
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Webhook-Secret") or request.query.get("secret", "")
        if supplied != WEBHOOK_SECRET:
            raise web.HTTPUnauthorized()
    try:
        data = await request.json()
        result = await process_lead(request.app["tg"], data)
        return web.json_response(result)
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def health(_):
    return web.json_response({"ok": True, "service": "lead-bot-v1-auto-distribute"})

async def backup_edited_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await backup_edited_router(update, context)



async def team_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.effective_message.reply_text("请在要接 Lead 的团队群使用 /team_add A")
        return
    bucket = (context.args[0] if context.args else "A").upper()
    persistent_team_add(chat.id, chat.title or str(chat.id), bucket)
    # Bridge to the canonical Round Robin table used by process_lead().
    with db() as c:
        old = c.execute("SELECT position FROM teams WHERE chat_id=?", (chat.id,)).fetchone()
        if old:
            pos = old["position"]
        else:
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 n FROM teams").fetchone()["n"]
        canonical_name = f"{bucket}｜{chat.title or chat.id}"
        c.execute(
            """INSERT OR REPLACE INTO teams(chat_id,name,enabled,position,created_at)
               VALUES(?,?,1,?,?)""",
            (chat.id, canonical_name, pos, now_iso()),
        )
    await update.effective_message.reply_text(
        f"✅ 团队群已永久加入 {bucket} 组\n"
        f"群：{chat.title or chat.id}"
    )

async def teams_persistent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bucket = (context.args[0] if context.args else "A").upper()
    rows = persistent_teams(bucket)
    if not rows:
        await update.effective_message.reply_text(f"📭 {bucket} 组暂无团队群")
        return
    lines = [f"• {r['title']} ({r['chat_id']})" for r in rows]
    await update.effective_message.reply_text(
        f"🏢 {bucket} 组团队群\n" + "\n".join(lines)
    )


async def persist_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inbox = lead_inbox_get()
    route = backup_get_route()
    a = persistent_teams("A")
    b = persistent_teams("B")
    c = persistent_teams("C")
    await update.effective_message.reply_text(
        "💾 持久配置状态\n"
        f"Lead入口：{'✅' if inbox and inbox['chat_id'] and inbox['enabled'] else '❌'}\n"
        f"Backup主群：{'✅' if route and route['source_chat_id'] else '❌'}\n"
        f"Backup群：{'✅' if route and route['backup_chat_id'] else '❌'}\n"
        f"A组团队：{len(a)}\n"
        f"B组团队：{len(b)}\n"
        f"C组团队：{len(c)}\n"
        f"数据库：{DB_PATH}"
    )



def normalize_my_mobile(raw, allow_missing_trunk=False):
    """Normalize a Malaysian mobile number to +60 format.

    Normal text scanning stays conservative by default so a numeric Code is not
    mistaken for a phone.  Callers that are already looking at an explicit phone
    column/field may set ``allow_missing_trunk=True`` so Excel values such as
    196025824 are restored to 0196025824 automatically.
    """
    if raw is None:
        return None
    digits = _clean_phone_digits(raw)
    if digits.startswith("01") and len(digits) in (10, 11):
        digits = "60" + digits[1:]
    elif allow_missing_trunk and digits.startswith("1") and len(digits) in (9, 10):
        digits = "60" + digits
    if not (digits.startswith("601") and len(digits) in (11, 12)):
        return None
    local = digits[2:]
    if len(set(local)) <= 2 or re.fullmatch(r"(123)+", local):
        return None
    return "+" + digits



# Manual intake correction is scoped to the S inbox shown in the report.
S_MANUAL_INBOX_ID = -1004362293045
ANTISCAM_STATUS_CHAT_ID = -1003917773643

def _s_normalize_phone(raw):
    """Normalize S-inbox MY mobile numbers without changing global phone rules.

    Besides the normal 01... / 60... forms, the manual F/T sheet can contain
    a local mobile with the leading zero omitted (for example 196025824).  In
    that case restore the local zero before handing it to the existing normalizer.
    """
    # This function is used only for the explicit S-manual phone position, so the
    # missing local trunk zero can be restored safely without treating Code as phone.
    return normalize_my_mobile(raw, allow_missing_trunk=True)



def _s_platform_marker(value):
    """Return FB/TK for an explicit small source marker; otherwise None."""
    value = str(value or '').strip()
    value = re.sub(r'(?i)^(?:source|platform|来源|來源|平台)\s*[:：]\s*', '', value).strip()
    key = re.sub(r'\s+', '', value).casefold()
    if key in ('f', 'fb', 'facebook'):
        return 'FB'
    if key in ('t', 'tk', 'tiktok'):
        return 'TK'
    return None


def _s_unwrap_platform(text):
    """Detect the user's explicit F/T marker at either edge and remove only that marker.

    Supported examples (case-insensitive):
      f <record> / t <record>
      <record> f / <record> t
      FB / Facebook / TK / TikTok at either edge
      f1789239814 ... / t1789239814 ...
      ... 13/09/2026 03:03:34f
    The rest of the lead is preserved verbatim for the source-specific parser.
    """
    raw = str(text or '').strip()
    if not raw:
        return '', None

    # Telegram formatting is normally metadata, but tolerate literal Markdown
    # copied through another app by removing paired bold markers per line.
    cleaned_lines = []
    for line in raw.replace('\r', '').splitlines():
        value = line.strip()
        if value.startswith('**') and value.endswith('**') and len(value) >= 4:
            value = value[2:-2].strip()
        cleaned_lines.append(value)
    raw = '\n'.join(cleaned_lines).strip()
    marker = None

    # 1) Explicit standalone/word marker at the beginning.
    # Require a separator after the word marker so names/descriptions are never chopped.
    m = re.match(r'(?is)^\s*(f|fb|facebook|t|tk|tiktok)(?:\s+|\t+)(.+)$', raw)
    if m:
        marker = _s_platform_marker(m.group(1))
        raw = m.group(2).strip()
    else:
        # Also accept F/T attached directly to the numeric record Code.
        m = re.match(r'(?is)^\s*([ft])(?=\d{8,20}(?:\s|\t|$))', raw)
        if m:
            marker = _s_platform_marker(m.group(1))
            raw = raw[m.end():].strip()

    # 2) Explicit marker at the end. This is the user's normal small-marker layout.
    # A conflicting second edge marker is not allowed to silently overwrite the first.
    m = re.search(r'(?is)(?:\s+|\t+)(f|fb|facebook|t|tk|tiktok)\s*$', raw)
    if m:
        end_marker = _s_platform_marker(m.group(1))
        if marker is None or marker == end_marker:
            marker = end_marker
            raw = raw[:m.start()].strip()
    else:
        # Accept a single F/T attached to the end of a complete date/time.
        m = re.search(
            r'(?is)((?:\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})'
            r'(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s*(?:AM|PM)|Z|[+-]\d{2}:?\d{2})?)?)'
            r'([ft])\s*$', raw
        )
        if m:
            end_marker = _s_platform_marker(m.group(2))
            if marker is None or marker == end_marker:
                marker = end_marker
                raw = raw[:m.start(2)].strip()

    # 3) Historical wrapper form: same small marker on both ends (f...f / t...t).
    # This runs only when neither normal edge rule already claimed a marker.
    if marker is None and len(raw) > 2:
        first = raw[:1].casefold()
        last = raw[-1:].casefold()
        if first == last and first in ('f', 't'):
            inner = raw[1:-1].strip()
            # Only treat it as a wrapper when the inner record starts with a numeric Code.
            if re.match(r'^\d{8,20}(?:\s|\t|$)', inner):
                marker = 'TK' if first == 't' else 'FB'
                raw = inner

    # Clean paired Markdown one more time after marker removal.
    out_lines = []
    for line in raw.splitlines():
        value = line.strip()
        if value.startswith('**') and value.endswith('**') and len(value) >= 4:
            value = value[2:-2].strip()
        out_lines.append(value)
    return '\n'.join(out_lines).strip(), marker


def _s_schema_text(text):
    """Parse the marked manual row using the fixed business schema.

    Business columns are always:
      Code | Nama | Nombor WhatsApp | Amount | Scam description | Date

    The user's F/T marker can be before or after the row and is handled first.
    F means Facebook (FB); T means TikTok (TK).  The two sources therefore never
    need to guess one another from ad text.  Excel tabs and Telegram-flattened
    rows separated by 2+ spaces are both accepted.
    """
    cleaned, wrapped_platform = _s_unwrap_platform(text)
    if not cleaned:
        return None

    # This helper is intentionally only for one fixed manual record. Labelled/multiline
    # leads continue through the existing parser unchanged.
    candidate = None
    markers = [wrapped_platform] if wrapped_platform else []

    if '\t' in cleaned:
        import csv
        rows = list(csv.reader(cleaned.splitlines(), delimiter='\t'))
        for row in rows:
            cells = [str(value or '').strip() for value in row]
            while cells and not cells[-1]:
                cells.pop()
            if not cells:
                continue
            if len(cells) == 1 and _s_metadata_line(cells[0])[0] == 'platform':
                markers.append(_s_metadata_line(cells[0])[1])
                continue
            if cells[0].casefold() == 'code' and len(cells) > 2 and cells[1].casefold() == 'nama':
                continue
            # Remove an explicit marker column if clipboard kept it as a cell.
            if cells and _s_platform_marker(cells[0]):
                markers.append(_s_platform_marker(cells[0])); cells = cells[1:]
            if cells and _s_platform_marker(cells[-1]):
                markers.append(_s_platform_marker(cells[-1])); cells = cells[:-1]
            if len(cells) < 5 or candidate is not None or not _s_normalize_phone(cells[2]):
                return None
            # Any columns after Date must be blank or source-marker metadata only.
            extras = [value for value in cells[6:] if value]
            for value in extras:
                platform = _s_platform_marker(value)
                if not platform:
                    return None
                markers.append(platform)
            # Fixed business schema tolerates an empty Amount cell. Some clipboard
            # paths collapse an empty Excel cell, yielding exactly five values:
            # Code | Name | Phone | Scam description | Date. Keep the positional
            # meaning instead of shifting every field left.
            if len(cells) == 5 and re.fullmatch(
                r'\d{1,2}[/-]\d{1,2}[/-]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
                cells[4], re.I
            ):
                candidate = [cells[0], cells[1], cells[2], '-', cells[3], cells[4]]
            else:
                candidate = cells[:6]
    else:
        # Telegram may preserve the copied Excel row either as one flattened line
        # or as one business column per line.  Both layouts use the same fixed schema:
        # Code | Name | Phone | Amount | Scam description | Date.
        if '\n' in cleaned:
            cells = [line.strip() for line in cleaned.splitlines() if line.strip()]
            if cells and _s_platform_marker(cells[0]):
                markers.append(_s_platform_marker(cells[0])); cells = cells[1:]
            if cells and _s_platform_marker(cells[-1]):
                markers.append(_s_platform_marker(cells[-1])); cells = cells[:-1]
            if len(cells) < 5 or len(cells) > 6:
                return None
            # If Amount was blank, Telegram may collapse the empty line and leave
            # Code | Name | Phone | Scam description | Date. Reinsert the empty
            # Amount placeholder so all later fields stay in their fixed columns.
            if len(cells) == 5 and re.fullmatch(
                r'\d{1,2}[/-]\d{1,2}[/-]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
                cells[4], re.I
            ):
                cells = [cells[0], cells[1], cells[2], '-', cells[3], cells[4]]
        else:
            # In a flattened row, 2+ spaces are normally the clipboard column boundary.
            cells = [x.strip() for x in re.split(r'\s{2,}', cleaned) if x.strip()]
            if cells and _s_platform_marker(cells[0]):
                markers.append(_s_platform_marker(cells[0])); cells = cells[1:]
            if cells and _s_platform_marker(cells[-1]):
                markers.append(_s_platform_marker(cells[-1])); cells = cells[:-1]

            # Some Telegram/Excel copies collapse every column separator to ONE space.
            # Recover only the user's fixed business schema:
            # Code | Name | Phone | Amount | Scam description | Original date/time.
            # We anchor on Code + a real MY phone + a trailing date/time, so names and
            # descriptions keep their exact wording instead of being guessed/re-written.
            if len(cells) < 5:
                one = cleaned.strip()
                code_m = re.match(r'^(\d{6,20})\s+', one)
                date_m = re.search(
                    r'(\d{1,2}[/-]\d{1,2}[/-]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)\s*$',
                    one, re.I
                )
                phone_m = None
                if code_m and date_m:
                    middle = one[code_m.end():date_m.start()].strip()
                    for pm in re.finditer(r'(?<!\d)(?:(?:\+?60)1(?:[ .()-]?\d){8,9}|01(?:[ .()-]?\d){8,9})(?!\d)', middle):
                        if _s_normalize_phone(pm.group(0)):
                            phone_m = pm
                            break
                    if phone_m:
                        code = code_m.group(1)
                        name = middle[:phone_m.start()].strip()
                        phone = phone_m.group(0).strip()
                        tail = middle[phone_m.end():].strip()
                        # Amount is the 4th fixed business column. Do not require RM:
                        # Forms users often type free-form values such as
                        # "56ribu ringgit Malaysia", "56 ribu", "56k" or
                        # "Lebih RM50,000". Preserve the wording exactly and use
                        # only the following text as the scam-description column.
                        amount_re = re.compile(
                            r'(?i)^((?:(?:lebih|hampir|kurang(?:\s+lebih)?|bawah|atas)\s+)?'
                            # Currency may be written as RM210K, USD80k, MYR 5000,
                            # SGD1,200, etc. Keep the user's wording unchanged.
                            r'(?:(?:RM|MYR|USD|SGD|USDT)\s*)?[0-9][0-9,.]*'
                            r'(?:\s*(?:k|rb|ribu))?'
                            r'(?:\s+ringgit)?(?:\s+malaysia)?'
                            r'|banyak\s+juga|banyak|tidak\s+pasti)\s+(.+)$'
                        )
                        am = amount_re.match(tail)
                        if name and am:
                            amount = am.group(1).strip()
                            description = am.group(2).strip()
                            cells = [code, name, phone, amount, description, date_m.group(1).strip()]
                        elif name and tail:
                            # Amount is optional in the form. When it is blank,
                            # preserve the entire post-phone text as the customer's
                            # scam description instead of abandoning the fixed schema
                            # and falling back to the old positional formatter.
                            cells = [code, name, phone, '-', tail, date_m.group(1).strip()]
        if len(cells) < 5 or not re.fullmatch(r'\d{6,20}', cells[0]) or not _s_normalize_phone(cells[2]):
            return None
        candidate = cells[:6]

    if not candidate:
        return None

    # Reject conflicting source markers instead of randomly calling Facebook/TikTok.
    explicit = [m for m in markers if m in ('FB', 'TK')]
    if explicit and len(set(explicit)) > 1:
        return None
    platform = explicit[0] if explicit else None

    code, name, phone, amount, description = candidate[:5]
    date = candidate[5] if len(candidate) >= 6 else ''
    parts = ([platform] if platform else []) + [
        f'编号：{code}',
        f'Nama: {name}',
        f'Contact Number: {phone}',
        f'Jumlah wang yang ditipu: {amount}',
        f'Bagaimana anda ditipu: {description}',
    ]
    if date:
        parts.append(f'Date: {date}')
    return '\n'.join(parts)

def _s_lead_phones(text):
    """Validate whole numeric runs within a line; never join money on another line."""
    found, seen = [], set()
    cleaned, wrapped_platform = _s_unwrap_platform(text)
    parse_text = _s_schema_text(text) or cleaned
    if wrapped_platform:
        parse_text = wrapped_platform + "\n" + parse_text
    for line in parse_text.splitlines():
        if re.match(r"(?i)^\s*(?:id|reference(?: id)?|编号|編號|amount(?: lost)?|jumlah wang yang ditipu|金额|金額)\s*[:：]", line):
            continue
        for match in re.finditer(r"(?<![\w+])\+?\d[\d \t().-]*\d(?!\w)", line):
            raw = match.group().strip()
            normalized = _s_normalize_phone(raw)
            if normalized and normalized not in seen:
                found.append((raw, normalized))
                seen.add(normalized)
    return found


def _s_metadata_line(line):
    """Recognize explicit source markers and standalone import timestamps only."""
    value = (line or '').strip()
    platform = re.sub(r'(?i)^(?:source|platform|来源|來源|平台)\s*[:：]\s*', '', value)
    key = re.sub(r'\s+', '', platform).casefold()
    if key in ('f', 'fb', 'facebook'):
        return 'platform', 'FB'
    if key in ('t', 'tk', 'tiktok'):
        return 'platform', 'TK'
    date_value = re.sub(r'(?i)^(?:date|created(?:[ _]time|[ _]at)?|日期|时间|時間)\s*[:：]\s*', '', value)
    if re.fullmatch(r'(?:\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s*(?:AM|PM)|Z|[+-]\d{2}:?\d{2})?)?', date_value, re.I):
        return 'date', date_value
    return None, None

def _s_manual_fields(text, phone):
    """Read labels and the name-before-phone layout; retain uncertain text as details."""
    cleaned, wrapped_platform = _s_unwrap_platform(text)
    parse_text = _s_schema_text(text) or cleaned
    if wrapped_platform:
        parse_text = wrapped_platform + "\n" + parse_text
    lines = [line.strip() for line in parse_text.splitlines() if line.strip()]
    fields, used = {}, set()
    platforms = set()
    for i, line in enumerate(lines):
        kind, value = _s_metadata_line(line)
        if kind:
            used.add(i)
            if kind == 'platform':
                platforms.add(value)
            elif kind == 'date' and not fields.get('date'):
                # Preserve the Lead's own source/original timestamp.
                fields['date'] = value
    fields['platform'] = next(iter(platforms)) if len(platforms) == 1 else ('待确认' if platforms else '未注明')
    labels = {
        "name": r"customer name|full name|name|nama|姓名|名字",
        "amount": r"amount lost|amount|jumlah wang yang ditipu|金额|金額|损失金额|損失金額",
        "scam_type": r"scam type|bagaimana anda ditipu|诈骗类型|詐騙類型",
        "email": r"email|e-mail|邮箱|郵箱",
        "reference": r"id|reference(?: id)?|编号|編號",
    }
    for i, line in enumerate(lines):
        for field, pattern in labels.items():
            match = re.fullmatch(rf"(?:{pattern})\s*[:：]\s*(.+)", line, re.I)
            if match and field not in fields:
                fields[field] = match.group(1).strip()
                used.add(i)
                break
    phone_indices = [i for i, line in enumerate(lines)
                     if any(n == phone for _, n in _s_lead_phones(line))]
    for i in phone_indices:
        # Consume only a phone-only line or an explicit phone label.
        content = re.sub(r"(?i)^(?:contact number|phone|mobile|telephone|tel|telefon|电话|電話|手机|手機)\s*[:：]\s*", "", lines[i])
        digits = re.sub(r"\D", "", content)
        if re.fullmatch(r"[+\d \t().-]+", content) and _s_normalize_phone(content) == phone:
            used.add(i)
        if "name" not in fields and i > 0 and i - 1 not in used:
            candidate = lines[i - 1]
            # Only infer a short name in the observed name -> phone layout.
            if len(candidate) <= 80 and 1 <= len(candidate.split()) <= 8 and all(c.isalpha() or c in " '-’." for c in candidate):
                fields['name'] = candidate
                used.add(i - 1)
    # A leading bare record ID before the inferred/labeled name is metadata.
    first_content = next((i for i in range(len(lines)) if _s_metadata_line(lines[i])[0] is None), None)
    if (first_content is not None and re.fullmatch(r"\d{8,20}", lines[first_content])
            and first_content not in used and 'name' in fields and phone_indices
            and first_content < phone_indices[0]):
        fields.setdefault('reference', lines[first_content])
        used.add(first_content)
    amounts = [(i, line) for i, line in enumerate(lines) if i not in used
               and re.fullmatch(r"(?:RM\s*)?\d+(?:,\d{3})*(?:\.\d{1,2})?\s*(?:k|rb|ribu)?", line, re.I)]
    if 'amount' not in fields and len(amounts) == 1:
        index, value = amounts[0]
        fields['amount'] = value
        used.add(index)
    if 'email' not in fields:
        emails = [(i, line) for i, line in enumerate(lines) if i not in used and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", line)]
        if len(emails) == 1:
            index, value = emails[0]
            fields['email'] = value
            used.add(index)
    fields['details'] = '\n'.join(line for i, line in enumerate(lines) if i not in used)
    # In the manual layout, text left after name/contact/amount is the
    # supplied scam description. Preserve its wording instead of inventing a category.
    if (not fields.get('scam_type') and fields.get('name') and fields.get('amount')
            and fields['details'] and not re.search(r'(?m)^\s*\d[\d,.]*\s*$', fields['details'])):
        fields['scam_type'] = fields['details']
        fields['details'] = ''
    return fields

def _s_manual_card(text, phone):
    fields = _s_manual_fields(text, phone)
    card = [f"📋 Customer Information (Lead Form) {fields['platform']}", "",
            f"Scam Type: {fields.get('scam_type', '-')}",
            f"Amount Lost: {fields.get('amount', '-')}",
            f"Customer Name: {fields.get('name', '未确认 / Unconfirmed')}",
            f"Contact Number: {phone}"]
    if fields.get('email'):
        card.append(f"Email: {fields['email']}")
    # Import IDs and dates stay in the stored original text, not the staff card.
    if fields['details']:
        card.extend(["", "Details / 原文说明:", fields['details']])
    return '\n'.join(card)

def extract_and_validate_lead_phone(text):
    """
    Find a phone-like token from the Lead text, normalize it,
    and return (normalized, raw_candidate).
    """
    if not text:
        return None, None

    # Fixed six-column anti-scam row safety net:
    #   Code | Nama | Nombor WhatsApp | Amount | Scam description | Date
    # Even when a future/free-form Amount prevents the full schema parser from
    # succeeding, the first numeric field is still the Code and must NEVER be
    # normalized as a Malaysian phone.  Search only after the leading Code and
    # before the trailing source date; the first valid MY mobile there is the
    # explicit WhatsApp column.
    try:
        _cleaned, _platform = _s_unwrap_platform(text)
        _code_m = re.match(r'^\s*(\d{6,20})\s+', _cleaned or '')
        _date_m = re.search(
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)\s*$',
            _cleaned or '', re.I
        )
        if _code_m and _date_m and _date_m.start() > _code_m.end():
            _middle = _cleaned[_code_m.end():_date_m.start()]
            for _pm in re.finditer(r'(?<!\d)(?:(?:\+?60)1(?:[ .()-]?\d){8,9}|01(?:[ .()-]?\d){8,9})(?!\d)', _middle):
                _raw = _pm.group(0).strip()
                _norm = normalize_my_mobile(_raw, allow_missing_trunk=False)
                if _norm:
                    return _norm, _raw
    except Exception:
        LOG.exception("fixed-row phone safety parse failed")

    # An explicit phone label is authoritative.  Here we can safely restore a
    # leading Malaysian 0 that Excel/Forms dropped, without confusing a Code/ID.
    labelled = re.search(
        r"(?im)^\s*(?:contact number|phone(?: number)?|mobile|whatsapp|nombor whatsapp|telefon|tel|电话|電話|手机|手機)\s*[:：=-]\s*(.+?)\s*$",
        text,
    )
    if labelled:
        raw_labelled = labelled.group(1).strip()
        normalized = normalize_my_mobile(raw_labelled, allow_missing_trunk=True)
        if normalized:
            return normalized, raw_labelled

    # Prefer lines/tokens that look phone-ish and are at least 8 digits.
    candidates = re.findall(r"\+?\d[\d\s\-\(\)]{7,16}\d", text)
    for cand in reversed(candidates):
        normalized = normalize_my_mobile(cand)
        if normalized:
            return normalized, cand

    # If we found numeric-looking content but none is valid, surface the last one.
    fallback = None
    raw_nums = re.findall(r"\+?\d{8,15}", re.sub(r"[\s\-\(\)]", "", text))
    if raw_nums:
        fallback = raw_nums[-1]
    return None, fallback


def lead_once_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lead_processed_messages(
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            processed_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, message_id)
        )""")

def lead_once_claim(chat_id, message_id):
    """Return True only for the first processor that claims this Telegram message."""
    lead_once_init()
    with db() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO lead_processed_messages(chat_id,message_id,processed_at)
               VALUES(?,?,?)""",
            (int(chat_id), int(message_id), now_iso())
        )
        return cur.rowcount == 1



def _looks_like_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(value or "").strip()))

def _copied_excel_row_to_lead(text: str, entry_chat_id: int):
    """Parse a whole Excel row pasted into Telegram (tabs OR flattened spaces)."""
    raw = (text or "").replace("\r", "").strip()
    if not raw:
        return None

    # Excel desktop normally copies TSV, but Telegram/clipboard can flatten tabs to spaces.
    if "\t" in raw:
        cells = [str(x or "").strip() for x in raw.split("\t")]
        start = 0
        for i, v in enumerate(cells):
            if v.casefold() in {"fb", "facebook"}:
                start = i + 1
        tail = [v for v in cells[start:] if v and v.upper() not in {"CREATED", "LEAD", "FALSE", "TRUE"}]
        email = next((v for v in tail if _looks_like_email(v)), "")
        phone = next((v for v in tail if normalize_my_mobile(v)), "")
        if not phone:
            return None
        human = [v for v in tail if v not in {email, phone} and not re.match(r"(?i)^(?:l|ag|as|c|f):\d+", v)]
        def amountish(v):
            # Amount must look like an amount, not merely contain a digit. This avoids
            # treating scam descriptions such as "Loan scammer buat 2 agreement" as money.
            x = re.sub(r"\s+", " ", str(v or "").strip()).casefold()
            return bool(
                re.fullmatch(r"(?:rm\s*)?[0-9][0-9, .]*(?:k|rb|ribu|\+\+)?", x)
                or re.fullmatch(r"(?:lebih|hampir|kurang(?:\s+lebih)?|bawah|atas)\s+(?:rm\s*)?[0-9][0-9, .]*(?:k|rb|ribu|\+\+)?", x)
                or re.fullmatch(r"rm[0-9, .+\-]+", x)
                or re.fullmatch(r"rm[0-9, .]+\s*-\s*rm[0-9, .]+", x)
                or re.fullmatch(r"(?:banyak juga|banyak|tidak pasti)", x)
            ) and len(x) <= 100
        amount = next((v for v in human if amountish(v)), "-")
        remaining = [v for v in human if v != amount]
        # The customer name is the final non-metadata value around the contact fields.
        name = remaining[-1] if remaining else "未填写"
        desc = [v for v in remaining[:-1] if v and v.upper() not in {"CREATED", "LEAD"}]
        scam_type = " ".join(desc).strip() if desc else "-"
    else:
        # Flattened row. Drop all Meta/ad metadata through the final platform marker `fb`.
        mfb = list(re.finditer(r"(?i)(?:^|\s)fb(?:\s|$)", raw))
        if not mfb:
            return None
        tail_text = raw[mfb[-1].end():].strip()
        tail_text = re.sub(r"(?i)\s+(?:CREATED|LEAD)\s*$", "", tail_text).strip()

        em = re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", tail_text)
        pm = None
        for mm in re.finditer(r"(?<!\d)(?:\+?60|0)1\d[\d .-]{7,12}(?!\d)", tail_text):
            if normalize_my_mobile(mm.group(0)):
                pm = mm; break
        if not pm:
            return None
        email = em.group(0) if em else ""
        phone = pm.group(0).strip()

        markers = sorted([(pm.start(), pm.end(), 'phone')] + ([(em.start(), em.end(), 'email')] if em else []))
        first_s, first_e, first_kind = markers[0]
        before = tail_text[:first_s].strip()
        after_first = tail_text[first_e:].strip()
        if len(markers) == 2:
            second_s, second_e, _ = markers[1]
            between = tail_text[first_e:second_s].strip()
            after = tail_text[second_e:].strip()
        else:
            between, after = "", after_first

        # Name is normally after the last contact field; if phone is last, it is between email and phone.
        name = after if after else between
        name = name.strip(" -|,") or "未填写"

        # Before the first contact are amount + scam type (order varies between exports).
        amount = "-"; scam_type = before or "-"
        ma = re.match(r"(?i)^((?:RM\s*)?[\d,.]+(?:\s*(?:k|ribu|rb))?|Banyak\s+juga|lebih\s+rm\S*)\s+(.+)$", before)
        if ma:
            amount, scam_type = ma.group(1).strip(), ma.group(2).strip()
        else:
            # Common alternate export: scam type first, amount last.
            ma2 = re.match(r"(?i)^(.+?)\s+((?:RM\s*)?[\d,.]+(?:\s*(?:k|ribu|rb))?|Banyak\s+juga|lebih\s+rm\S*)$", before)
            if ma2:
                scam_type, amount = ma2.group(1).strip(), ma2.group(2).strip()

    card = (
        "📋 Customer Information (Lead Form)\n\n"
        f"Scam Type: {scam_type}\n"
        f"Amount Lost: {amount}\n"
        f"Email: {email or '-'}\n"
        f"Customer Name: {name}\n"
        f"Contact Number: {phone}"
    )
    external_id_m = re.search(r"(?i)\bl:\d+", raw)
    created_m = re.search(r"20\d{2}-\d{2}-\d{2}T[^\s]+", raw)
    return {
        "phone": phone, "name": name, "full_name": name,
        "source": "Excel Row Paste", "route_key": "antiscam",
        "entry_chat_id": entry_chat_id, "raw_text": card,
        "external_id": external_id_m.group(0) if external_id_m else "",
        "created_time": created_m.group(0) if created_m else "",
        "scam_type": scam_type, "amount_lost": amount, "email": email,
    }

async def s_came_query_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """High-priority duplicate lookup for every configured antiscam Lead inbox."""
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type == "private":
        return
    text = (msg.text or "").strip()
    came_platform = _s_parse_came_query(text)
    if came_platform is None:
        return
    cid = int(chat.id)
    if not _came_query_allowed(cid):
        return
    await _reply_long_text(msg, _s_today_came_text(cid, came_platform, full=False))
    raise ApplicationHandlerStop


async def s_came_mode_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """完整 or /完整 -> let the requester choose 简易版 or 完整版 themselves."""
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or chat.type == "private":
        return
    text = re.sub(r"^/+", "", (msg.text or "").strip())
    if text not in ("完整", "完整模式", "来过完整", "來過完整"):
        return
    cid = int(chat.id)
    if not _came_query_allowed(cid):
        return
    uid = int(user.id)
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("📄 简易版", callback_data=f"camefmt:s:{uid}"),
        InlineKeyboardButton("📋 完整版", callback_data=f"camefmt:f:{uid}"),
    ]])
    await msg.reply_text("请选择你要查看的来过报表：", reply_markup=markup)
    raise ApplicationHandlerStop


async def s_came_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.message:
        return
    try:
        _, mode, raw_uid = (q.data or "").split(":", 2)
        owner_uid = int(raw_uid)
    except Exception:
        await q.answer()
        return
    click_uid = int(update.effective_user.id) if update.effective_user else 0
    if click_uid != owner_uid:
        await q.answer("只有刚才发『完整』的人可以选择。", show_alert=True)
        return
    cid = int(q.message.chat.id)
    if not _came_query_allowed(cid):
        await q.answer("这个群没有启用来过查询。", show_alert=True)
        return
    await q.answer()
    full = (mode == "f")
    report = _s_today_came_text(cid, "", full=full)
    label = "📋 完整版" if full else "📄 简易版"
    try:
        await q.edit_message_text(f"✅ 已选择 {label}")
    except Exception:
        pass
    await _reply_long_text(q.message, report)


async def lead_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user or chat.type == "private":
        return

    text = (msg.text or "").strip()
    if not text:
        return

    # S inbox quick lookup is normally handled by the high-priority router above.
    # Keep this fallback for compatibility, including optional leading slash.
    # This is a read-only query and must run before Lead phone validation.
    if _came_query_allowed(chat.id):
        came_platform = _s_parse_came_query(text)
        if came_platform is not None:
            await _reply_long_text(msg, _s_today_came_text(chat.id, came_platform, full=False))
            return

    if text.startswith("/"):
        return

    # Only ordinary, non-reply Leads.
    if msg.reply_to_message:
        return

    # Smart fixed-schema intake: a complete Lead row with an explicit FB/TK marker
    # may be submitted from any normal Lead group, even before that group is bound.
    # Business columns are fixed by the user's sheet:
    # Code | Nama | Nombor WhatsApp | Jumlah wang yang ditipu | Bagaimana anda ditipu | Date.
    # Protect non-Lead operational groups from accidental intake.
    _schema_text = _s_schema_text(text)
    _schema_marker = _s_unwrap_platform(text)[1]
    _smart_schema_lead = bool(_schema_text and _schema_marker in ("FB", "TK"))
    _protected_nonlead_chats = {
        -1003941232666,  # 888 main
        -1004348567725,  # 888 backup
        -1004482054615,  # company ledger
        -1004444989940,  # secondary company ledger
    }
    if not lead_inbox_is_active(chat.id) and not (
        _smart_schema_lead and int(chat.id) not in _protected_nonlead_chats
    ):
        return

    # Pass the fixed-schema decision to the canonical intake path for this update only.
    context.chat_data["smart_antiscam_schema"] = _smart_schema_lead

    # Claim only after we know this really is a Lead candidate.
    if not lead_once_claim(chat.id, msg.message_id):
        return

    # Fast manual Excel-row intake: select one whole row in Excel, Copy, paste to the
    # anti-scam inbox. The bot strips ad/platform/status metadata and keeps only the card.
    if (inbox_route_get(chat.id) == "antiscam" and ("\t" in text or re.search(r"(?i)\bfb\b", text))
            and not _smart_schema_lead
            and not (int(chat.id) == S_MANUAL_INBOX_ID and (
                _s_schema_text(text) is not None or
                ("\t" not in text and any(_s_metadata_line(line)[0] == 'platform' for line in text.splitlines()))))):
        row_lead = _copied_excel_row_to_lead(text, chat.id)
        if row_lead:
            normalized_phone = normalize_my_mobile(row_lead.get("phone", ""))
            if not normalized_phone:
                await msg.reply_text("❌ Excel 行里的马来西亚手机号码无效，请修正号码后再贴一次。")
                return
            await process_lead(
                context.application, row_lead, submitted_by=user.id, notify_duplicate_owner=False
            )
            return

    # Validate phone before duplicate detection / distribution.
    if int(chat.id) == S_MANUAL_INBOX_ID:
        candidates = _s_lead_phones(text)
        raw_phone, normalized_phone = candidates[0] if candidates else (None, None)
    else:
        normalized_phone, raw_phone = extract_and_validate_lead_phone(text)
    if not normalized_phone:
        await msg.reply_text(
            "❌ 无效号码 / Invalid phone\n"
            f"号码：{raw_phone or '未找到'}\n"
            "请使用马来西亚手机号码，例如：0123456789 / 60123456789 / +60123456789"
        )
        return

    # Make normalized phone available to downstream code without changing
    # the existing Lead business pipeline.
    context.chat_data["normalized_lead_phone"] = normalized_phone


    # One and only one entry into the existing Lead business pipeline.
    await _process_inbox_message(update, context.application)


async def _flush_backup_album(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    src = int(data["src"])
    dst = int(data["dst"])
    media_group_id = str(data["media_group_id"])
    key = (src, media_group_id)

    item = _backup_album_jobs.pop(key, None)
    if not item:
        return

    message_ids = sorted(set(int(x) for x in item.get("message_ids", []) if x))
    if not message_ids:
        return

    # Copy the whole Telegram media group in one call whenever possible.
    try:
        await context.bot.copy_messages(
            chat_id=dst,
            from_chat_id=src,
            message_ids=message_ids,
        )
        LOG.info(
            "Backup album copied complete: src=%s dst=%s gid=%s count=%s",
            src, dst, media_group_id, len(message_ids)
        )
    except Exception:
        LOG.exception(
            "copy_messages failed; fallback to per-message copy: src=%s gid=%s",
            src, media_group_id
        )
        for mid in message_ids:
            try:
                await context.bot.copy_message(
                    chat_id=dst,
                    from_chat_id=src,
                    message_id=mid,
                )
            except Exception:
                LOG.exception("Backup album item copy failed: %s", mid)


async def backup_album_collector(update: Update, context: ContextTypes.DEFAULT_TYPE, dst_chat_id: int):
    """Collect the entire Telegram media_group before backing it up.

    Every new album item resets a longer debounce timer. This avoids copying only
    the first image when Telegram delivers album updates with small gaps.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or not msg.media_group_id:
        return False

    src = int(chat.id)
    gid = str(msg.media_group_id)
    key = (src, gid)

    item = _backup_album_jobs.setdefault(
        key,
        {
            "message_ids": [],
            "job": None,
            "last_seen_message_id": None,
        }
    )

    mid = int(msg.message_id)
    if mid not in item["message_ids"]:
        item["message_ids"].append(mid)
    item["last_seen_message_id"] = mid

    # Cancel the previous pending flush. Each newly-arrived album item extends
    # the wait so late Telegram updates are included in the same backup.
    old_job = item.get("job")
    if old_job:
        try:
            old_job.schedule_removal()
        except Exception:
            pass

    # 3.0s is intentionally longer than the old short debounce.
    # This is still near-instant for users, while much safer for 3-10 image albums.
    item["job"] = context.job_queue.run_once(
        _flush_backup_album,
        when=3.0,
        data={
            "src": src,
            "dst": int(dst_chat_id),
            "media_group_id": gid,
        },
        name=f"backup-album-complete-{src}-{gid}",
    )
    return True


def sos_tracker_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS sos_source_messages(
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(source_chat_id, source_message_id)
        )""")

def sos_track_source_message(chat_id, message_id):
    sos_tracker_init()
    with db() as c:
        c.execute(
            """INSERT OR IGNORE INTO sos_source_messages(
               source_chat_id, source_message_id, created_at
            ) VALUES(?,?,?)""",
            (int(chat_id), int(message_id), now_iso())
        )

def sos_all_source_messages(chat_id):
    sos_tracker_init()
    with db() as c:
        rows = c.execute(
            """SELECT source_message_id
               FROM sos_source_messages
               WHERE source_chat_id=?
               ORDER BY source_message_id""",
            (int(chat_id),)
        ).fetchall()
    return [int(r["source_message_id"]) for r in rows if r["source_message_id"]]

def sos_forget_source_message(chat_id, message_id):
    sos_tracker_init()
    with db() as c:
        c.execute(
            "DELETE FROM sos_source_messages WHERE source_chat_id=? AND source_message_id=?",
            (int(chat_id), int(message_id))
        )

async def backup_auto_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Compatibility bridge for 888 -> 888 backup.

    IMPORTANT:
    Do not copy the message here.  The canonical backup_direct_router later in
    the handler chain owns text/photo/album copying, indexing and /find maps.
    This bridge only restores/synchronizes the persistent route so both old
    and new deployments use the same 888 route.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type == "private":
        return

    if msg.text and msg.text.startswith("/"):
        return

    route = backup_get_route()
    if (route and route["source_chat_id"] and route["backup_chat_id"]):
        return

    main_id = None
    backup_id = None

    # Recover from persistent V2 route if present.
    try:
        with db() as c:
            row = c.execute(
                "SELECT main_chat_id, backup_chat_id FROM persistent_backup_route LIMIT 1"
            ).fetchone()
            if row:
                main_id = row["main_chat_id"]
                backup_id = row["backup_chat_id"]
    except Exception:
        pass

    # Recover from legacy settings.
    if not main_id:
        for key in ("backup_main_chat_id", "backup_main_id", "main_backup_chat_id",
                    "backup_source_chat_id"):
            try:
                v = get_setting(key) or pget(key)
                if v:
                    main_id = int(v)
                    break
            except Exception:
                pass

    if not backup_id:
        for key in ("backup_chat_id", "backup_target_chat_id", "backup_group_id",
                    "backup_target_chat_id"):
            try:
                v = get_setting(key) or pget(key)
                if v:
                    backup_id = int(v)
                    break
            except Exception:
                pass

    if main_id and backup_id:
        backup_set_route(source_chat_id=int(main_id), backup_chat_id=int(backup_id))
        LOG.info(
            "Restored canonical backup route: %s -> %s",
            int(main_id), int(backup_id)
        )

    # Do NOT return ApplicationHandlerStop: backup_direct_router must receive
    # this same update and perform the actual canonical copy/index operation.
    return


def bot_admin_init():
    """Ensure the persistent Bot Admin table exists."""
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bot_admin_users(
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""")


def bot_admin_has(user_id):
    """Owner is always admin; other admins are read from the persistent DB."""
    if int(user_id) == int(OWNER_ID):
        return True
    bot_admin_init()
    with db() as c:
        row = c.execute(
            "SELECT 1 FROM bot_admin_users WHERE user_id=?",
            (int(user_id),)
        ).fetchone()
    return bool(row)


def find_auth_init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS find_authorized_users(
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            created_at TEXT
        )""")

def find_auth_has(user_id):
    uid = int(user_id)

    # Owner always has /find access.
    if uid == int(OWNER_ID):
        return True

    # Bot Admin assistants automatically have /find access.
    try:
        if bot_admin_has(uid):
            return True
    except Exception:
        pass

    # Explicit /find whitelist users also have access.
    find_auth_init()
    with db() as c:
        row = c.execute(
            "SELECT 1 FROM find_authorized_users WHERE user_id=?",
            (uid,)
        ).fetchone()
    return bool(row)

async def find_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    uid_actor = int(user.id)
    is_owner = (uid_actor == int(OWNER_ID))
    is_admin = False
    try:
        is_admin = bool(bot_admin_has(uid_actor))
    except Exception:
        is_admin = False

    if not (is_owner or is_admin):
        await msg.reply_text("⛔ 你没有管理 /find 用户的权限")
        return

    if not context.args:
        await msg.reply_text("用法：/find_add Telegram数字ID")
        return

    try:
        uid = int(context.args[0])
    except Exception:
        await msg.reply_text("❌ Telegram ID 必须是数字")
        return

    find_auth_init()
    with db() as c:
        c.execute(
            """INSERT OR REPLACE INTO find_authorized_users(
               user_id, added_by, created_at
            ) VALUES(?,?,?)""",
            (uid, uid_actor, now_iso())
        )

    await msg.reply_text(f"✅ 已授权 /find：{uid}")


async def find_del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    uid_actor = int(user.id)
    is_owner = (uid_actor == int(OWNER_ID))
    is_admin = False
    try:
        is_admin = bool(bot_admin_has(uid_actor))
    except Exception:
        is_admin = False

    if not (is_owner or is_admin):
        await msg.reply_text("⛔ 你没有管理 /find 用户的权限")
        return

    if not context.args:
        await msg.reply_text("用法：/find_del Telegram数字ID")
        return

    try:
        uid = int(context.args[0])
    except Exception:
        await msg.reply_text("❌ Telegram ID 必须是数字")
        return

    find_auth_init()
    with db() as c:
        c.execute(
            "DELETE FROM find_authorized_users WHERE user_id=?",
            (uid,)
        )

    await msg.reply_text(f"✅ 已取消 /find：{uid}")


async def find_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    uid_actor = int(user.id)
    is_owner = (uid_actor == int(OWNER_ID))
    is_admin = False
    try:
        is_admin = bool(bot_admin_has(uid_actor))
    except Exception:
        is_admin = False

    if not (is_owner or is_admin):
        await msg.reply_text("⛔ 你没有查看 /find 授权名单的权限")
        return

    find_auth_init()
    with db() as c:
        rows = c.execute(
            "SELECT user_id FROM find_authorized_users ORDER BY created_at"
        ).fetchall()

    text = f"🔐 /find 授权名单\nOwner：{OWNER_ID}（永久）"
    if rows:
        text += "\n" + "\n".join(f"• {r['user_id']}" for r in rows)
    else:
        text += "\n暂无额外授权用户"

    await msg.reply_text(text)


async def sos_self_destruct_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency burn: remove all tracked 888 source messages from the last 24 hours.
    Backup destination and /find indexes are never deleted.
    """
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return

    trigger = (msg.text or "").strip()
    if trigger.lower() != "sos" and trigger not in ("自毁", "999"):
        return

    # Same hidden authority: Owner/Admin/find-authorized users.
    if not find_auth_has(user.id):
        await msg.reply_text("⛔ 你没有紧急自焚权限")
        return

    route = backup_get_route()
    if not route or int(route["source_chat_id"] or 0) != int(chat.id):
        return

    sos_tracker_init()

    # Only messages tracked during the most recent 24 hours.
    with db() as c:
        rows = c.execute(
            """SELECT source_message_id
               FROM sos_source_messages
               WHERE source_chat_id=?
                 AND datetime(created_at) >= datetime('now','-24 hours')
               ORDER BY source_message_id""",
            (int(chat.id),)
        ).fetchall()
        source_ids = {
            int(r["source_message_id"]) for r in rows if r["source_message_id"]
        }

    # Do not include the trigger itself in the batch.
    source_ids.discard(int(msg.message_id))

    deleted = 0
    failed = 0
    for mid in sorted(source_ids):
        try:
            await context.bot.delete_message(
                chat_id=int(chat.id),
                message_id=int(mid)
            )
            deleted += 1
            sos_forget_source_message(chat.id, mid)
        except Exception as e:
            failed += 1
            LOG.error(
                "EMERGENCY_BURN_DELETE_FAIL chat=%s message_id=%s error=%r",
                chat.id, mid, e
            )

    try:
        await msg.delete()
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=int(chat.id),
            text=(
                "🔥 紧急自焚已执行\n"
                f"✅ 最近24小时已删除：{deleted} 条\n"
                f"⚠️ 删除失败：{failed} 条\n"
                "🔒 888 backup：保留\n"
                "🔎 /find：仍可找回"
            )
        )
    except Exception:
        pass


async def admin_add_raw_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Robust /admin_add <id> parser for group chats; bypasses route handlers."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    text = (msg.text or "").strip()
    m = re.match(r"^/admin_add(?:@\w+)?\s+(\d+)\s*$", text, re.I)
    if not m:
        return
    if int(user.id) != int(OWNER_ID):
        await msg.reply_text("⛔ 只有 Owner 可以添加 Admin")
        raise ApplicationHandlerStop
    uid = int(m.group(1))
    bot_admin_init()
    with db() as c:
        c.execute(
            """INSERT OR REPLACE INTO bot_admin_users(user_id,added_by,created_at)
               VALUES(?,?,?)""",
            (uid, int(OWNER_ID), now_iso())
        )
    await msg.reply_text(
        f"✅ 已添加 Admin 助理：{uid}\n"
        "可管理 /find 用户，但不能添加其他 Admin。"
    )
    raise ApplicationHandlerStop


async def admin_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ 只有 Owner 可以添加 Admin")
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/admin_add Telegram数字ID")
        return
    try:
        uid = int(context.args[0])
    except Exception:
        await update.effective_message.reply_text("❌ Telegram ID 必须是数字")
        return

    bot_admin_init()
    with db() as c:
        c.execute(
            """INSERT OR REPLACE INTO bot_admin_users(user_id,added_by,created_at)
               VALUES(?,?,?)""",
            (uid, int(OWNER_ID), now_iso())
        )

    await update.effective_message.reply_text(
        f"✅ 已添加 Admin 助理：{uid}\n"
        "可管理 /find 用户，但不能添加其他 Admin。"
    )

async def admin_remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ 只有 Owner 可以删除 Admin")
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/admin_remove Telegram数字ID")
        return
    try:
        uid = int(context.args[0])
    except Exception:
        await update.effective_message.reply_text("❌ Telegram ID 必须是数字")
        return

    bot_admin_init()
    with db() as c:
        c.execute("DELETE FROM bot_admin_users WHERE user_id=?", (uid,))

    await update.effective_message.reply_text(f"✅ 已删除 Admin 助理：{uid}")

async def admin_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ 只有 Owner 可以查看 Admin 名单")
        return

    bot_admin_init()
    with db() as c:
        rows = c.execute(
            "SELECT user_id,created_at FROM bot_admin_users ORDER BY created_at"
        ).fetchall()

    text = f"👑 Owner：{OWNER_ID}\n\n👥 Admin 助理"
    if rows:
        text += "\n" + "\n".join(f"• {r['user_id']}" for r in rows)
    else:
        text += "\n暂无"

    await update.effective_message.reply_text(text)


# ===== COMPANY CALCULATOR MODULE (isolated; additive only) =====
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from contextvars import ContextVar
import calendar

MONEY_Q = Decimal('0.01')
def D(v): return Decimal(str(v))
def money(v): return D(v).quantize(MONEY_Q, rounding=ROUND_HALF_UP)

def calc_init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS calc_config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calc_entries(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, chat_id INTEGER NOT NULL,
          user_id INTEGER, kind TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'RM',
          category TEXT, label TEXT, raw_text TEXT, settled INTEGER NOT NULL DEFAULT 1,
          settlement_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_calc_entries_created ON calc_entries(created_at);
        CREATE TABLE IF NOT EXISTS calc_staff_reports(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, chat_id INTEGER NOT NULL,
          staff TEXT NOT NULL, report_date TEXT NOT NULL, report_month TEXT NOT NULL,
          sale TEXT NOT NULL, expense15 TEXT NOT NULL, net_sale TEXT NOT NULL, com20 TEXT NOT NULL,
          salary TEXT, payable TEXT, status TEXT NOT NULL DEFAULT 'pending', raw_text TEXT,
          preview_message_id INTEGER, posted_entry_id INTEGER,
          UNIQUE(staff, report_month)
        );
        CREATE TABLE IF NOT EXISTS calc_staff_reports_v2(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, chat_id INTEGER NOT NULL,
          staff TEXT NOT NULL, report_date TEXT NOT NULL, report_month TEXT NOT NULL,
          sale TEXT NOT NULL, expense15 TEXT NOT NULL, net_sale TEXT NOT NULL, com20 TEXT NOT NULL,
          salary TEXT, payable TEXT, status TEXT NOT NULL DEFAULT 'pending', raw_text TEXT,
          preview_message_id INTEGER, posted_entry_id INTEGER,
          UNIQUE(chat_id,staff,report_month)
        );
        CREATE TABLE IF NOT EXISTS calc_report_sent(key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calc_entry_receipts(
          entry_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calc_cardfee_pending(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, chat_id INTEGER NOT NULL,
          user_id INTEGER, gross TEXT NOT NULL, fee TEXT NOT NULL, net TEXT NOT NULL,
          codes TEXT, raw_text TEXT, preview_message_id INTEGER,
          status TEXT NOT NULL DEFAULT 'pending', posted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS calc_chat_message_log(
          chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, message_time TEXT NOT NULL,
          has_digit INTEGER NOT NULL DEFAULT 0, protected INTEGER NOT NULL DEFAULT 0,
          text_preview TEXT, deleted INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(chat_id,message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_calc_chat_message_log_time
          ON calc_chat_message_log(chat_id,message_time,deleted,has_digit);
        CREATE TABLE IF NOT EXISTS calc_funder_advances(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, chat_id INTEGER NOT NULL,
          user_id INTEGER, funder_tag TEXT NOT NULL, amount TEXT NOT NULL,
          label TEXT, raw_text TEXT, record_date TEXT NOT NULL,
          settled INTEGER NOT NULL DEFAULT 0, settlement_entry_id INTEGER,
          source_entry_id INTEGER UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_calc_funder_advances_lookup
          ON calc_funder_advances(chat_id,funder_tag,record_date,settled);
        ''')
        # L/W Reply-attribution migration. A single Telegram message can contain
        # multiple dated expense lines, so source_message_id + source_line_no
        # identifies each advance without duplicating it on repeated replies.
        adv_cols={r['name'] for r in c.execute('PRAGMA table_info(calc_funder_advances)').fetchall()}
        if 'source_message_id' not in adv_cols:
            c.execute('ALTER TABLE calc_funder_advances ADD COLUMN source_message_id INTEGER')
        if 'source_line_no' not in adv_cols:
            c.execute('ALTER TABLE calc_funder_advances ADD COLUMN source_line_no INTEGER')
        if 'settled_at' not in adv_cols:
            c.execute('ALTER TABLE calc_funder_advances ADD COLUMN settled_at TEXT')
        c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_calc_funder_advances_source
          ON calc_funder_advances(chat_id,source_message_id,source_line_no)
          WHERE source_message_id IS NOT NULL''')
        # Ledger-date migration: allows a posted entry to be moved to its real accounting date
        # without changing the company balance. Existing rows keep using created_at.
        entry_cols={r['name'] for r in c.execute('PRAGMA table_info(calc_entries)').fetchall()}
        if 'record_date' not in entry_cols:
            c.execute('ALTER TABLE calc_entries ADD COLUMN record_date TEXT')
        c.execute('CREATE INDEX IF NOT EXISTS idx_calc_entries_record_date ON calc_entries(record_date)')

        # Receipt-link migration: keep both the bot receipt and the user's original +/- message.
        # This lets the user Reply either one with a date / undo command.
        receipt_cols={r['name'] for r in c.execute('PRAGMA table_info(calc_entry_receipts)').fetchall()}
        if 'source_message_id' not in receipt_cols:
            c.execute('ALTER TABLE calc_entry_receipts ADD COLUMN source_message_id INTEGER')
        c.execute('CREATE INDEX IF NOT EXISTS idx_calc_receipts_source ON calc_entry_receipts(chat_id,source_message_id)')

        # Card-fee detail migration: safe for existing databases.
        cols={r['name'] for r in c.execute('PRAGMA table_info(calc_cardfee_pending)').fetchall()}
        for name, typ in [('record_date','TEXT'),('customer_label','TEXT'),('card_details','TEXT'),('batch_id','TEXT'),('source_message_id','INTEGER')]:
            if name not in cols:
                c.execute(f'ALTER TABLE calc_cardfee_pending ADD COLUMN {name} {typ}')
        entry_cols={r['name'] for r in c.execute('PRAGMA table_info(calc_entries)').fetchall()}
        if 'batch_id' not in entry_cols:
            c.execute('ALTER TABLE calc_entries ADD COLUMN batch_id TEXT')
        if 'funder_tag' not in entry_cols:
            c.execute('ALTER TABLE calc_entries ADD COLUMN funder_tag TEXT')
        c.execute('CREATE INDEX IF NOT EXISTS idx_calc_entries_batch_id ON calc_entries(chat_id,batch_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_calc_entries_funder ON calc_entries(chat_id,funder_tag)')
        # Backward-safe migration: a formal +/- expense classified L/W is still
        # outstanding to that funder until the user explicitly settles it. It must
        # NOT be deducted again on settlement because it already hit company balance.
        # Settlement ledger entries themselves are not L/W advances.
        c.execute('''DELETE FROM calc_funder_advances
            WHERE source_entry_id IN (SELECT id FROM calc_entries WHERE COALESCE(category,'')='垫付结算')''')
        c.execute('''INSERT OR IGNORE INTO calc_funder_advances
            (created_at,chat_id,user_id,funder_tag,amount,label,raw_text,record_date,settled,settlement_entry_id,source_entry_id)
            SELECT created_at,chat_id,user_id,UPPER(funder_tag),amount,COALESCE(label,category,'未注明'),raw_text,
                   COALESCE(record_date,substr(created_at,1,10)),0,NULL,id
            FROM calc_entries
            WHERE UPPER(COALESCE(funder_tag,'')) IN ('L','W')
              AND COALESCE(category,'')!='垫付结算' ''')
        # Older builds used settled=1 + settlement_entry_id=source_entry_id merely
        # to prevent double-deduction. Convert those rows to real outstanding L/W.
        c.execute('''UPDATE calc_funder_advances
            SET settled=0, settlement_entry_id=NULL, settled_at=NULL
            WHERE source_entry_id IS NOT NULL
              AND settlement_entry_id=source_entry_id''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_calc_cardfee_batch_id ON calc_cardfee_pending(chat_id,batch_id)')
        c.execute("UPDATE calc_cardfee_pending SET batch_id='cf:'||id WHERE batch_id IS NULL OR batch_id='' ")
        # One-time/backward-safe copy of existing staff reports into the multi-company table.
        c.execute('''INSERT OR IGNORE INTO calc_staff_reports_v2
            (id,created_at,chat_id,staff,report_date,report_month,sale,expense15,net_sale,com20,salary,payable,status,raw_text,preview_message_id,posted_entry_id)
            SELECT id,created_at,chat_id,staff,report_date,report_month,sale,expense15,net_sale,com20,salary,payable,status,raw_text,preview_message_id,posted_entry_id
            FROM calc_staff_reports''')
        posted=c.execute("SELECT id,chat_id,raw_text,batch_id FROM calc_cardfee_pending WHERE status='posted' AND raw_text IS NOT NULL ORDER BY id").fetchall()
        for pr in posted:
            c.execute("UPDATE calc_entries SET batch_id=? WHERE chat_id=? AND raw_text=? AND (batch_id IS NULL OR batch_id='') AND (category IN ('卡费','卡费 Fee') OR label IN ('卡费 Gross','卡费 Fee'))",
                      (pr['batch_id'],pr['chat_id'],pr['raw_text']))

SECONDARY_CALC_CHAT_ID = -1004444989940
SECONDARY_CALC_NAME = '裕鑫 and 财富'
# Main company ledger: photo is evidence only; caption drives the accounting entry.
VOUCHER_CALC_CHAT_ID = -1004482054615
VOUCHER_CALC_NAME = '公司帐'
# The accounting feature set is shared by exactly these two chats, while all
# balances/entries/reports/searches stay isolated by chat_id.
FIXED_CALC_CHAT_IDS = {VOUCHER_CALC_CHAT_ID, SECONDARY_CALC_CHAT_ID}
_calc_scope_chat = ContextVar('calc_scope_chat', default=0)

def _raw_calc_get(key, default=None):
    with db() as c:
        r=c.execute('SELECT value FROM calc_config WHERE key=?',(key,)).fetchone()
    return r['value'] if r else default

def _raw_calc_set(key, value):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO calc_config(key,value) VALUES(?,?)',(key,str(value)))

def calc_primary_chat_id():
    # Keep the legacy un-prefixed config namespace permanently attached to
    # the original main company chat. This prevents a later /calculator_here
    # call from making another group inherit the main company's balance/config.
    return VOUCHER_CALC_CHAT_ID

def calc_chat_ids():
    # Only the two confirmed company-account groups use calculator/accounting.
    return set(FIXED_CALC_CHAT_IDS)

def calc_scope_chat_id():
    cid=int(_calc_scope_chat.get() or 0)
    return cid or calc_primary_chat_id()

def calc_get(key, default=None):
    cid=calc_scope_chat_id()
    primary=calc_primary_chat_id()
    storage_key=key if (not cid or cid==primary) else f'chat:{cid}:{key}'
    return _raw_calc_get(storage_key, default)

def calc_set(key, value):
    cid=calc_scope_chat_id()
    primary=calc_primary_chat_id()
    storage_key=key if (not cid or cid==primary) else f'chat:{cid}:{key}'
    _raw_calc_set(storage_key, value)

def calc_chat_id():
    return calc_scope_chat_id()

def calc_company_title(chat_id=None):
    cid=int(chat_id or calc_scope_chat_id() or 0)
    if cid==SECONDARY_CALC_CHAT_ID:
        return SECONDARY_CALC_NAME
    if cid==VOUCHER_CALC_CHAT_ID:
        return VOUCHER_CALC_NAME
    return '公司账'

def is_calc_chat(update):
    if not update.effective_chat: return False
    cid=int(update.effective_chat.id)
    if cid not in calc_chat_ids(): return False
    _calc_scope_chat.set(cid)
    return True

def _calc_message_local_time(msg):
    dt=getattr(msg, "date", None)
    if not dt:
        return datetime.now(TZ)
    try:
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ)
    except Exception:
        return datetime.now(TZ)

async def calc_message_audit_router(update, context):
    # Telegram Bot API cannot browse arbitrary history, so remember company
    # messages as they arrive. Cleanup protection is evaluated at delete time:
    # messages with AM/PM are treated as time-class chatter and may be deleted;
    # otherwise any digit protects the message.
    chat=update.effective_chat
    msg=update.effective_message
    if not chat or not msg or int(chat.id) not in FIXED_CALC_CHAT_IDS:
        return
    raw=(msg.text or msg.caption or "")
    has_digit=1 if re.search(r"\d", raw) else 0
    user=getattr(msg, "from_user", None)
    protected=1 if (user is None or bool(getattr(user, "is_bot", False))) else 0
    dt=_calc_message_local_time(msg)
    preview=(raw or "[media]")[:300]
    try:
        with db() as c:
            prev=c.execute("SELECT deleted FROM calc_chat_message_log WHERE chat_id=? AND message_id=?",
                           (int(chat.id),int(msg.message_id))).fetchone()
            deleted=int(prev["deleted"]) if prev else 0
            c.execute("""INSERT OR REPLACE INTO calc_chat_message_log
                (chat_id,message_id,message_time,has_digit,protected,text_preview,deleted)
                VALUES(?,?,?,?,?,?,?)""",
                (int(chat.id),int(msg.message_id),dt.strftime("%Y-%m-%d %H:%M:%S"),
                 has_digit,protected,preview,deleted))
    except Exception:
        LOG.exception("company message audit failed")

def _parse_cleanup_cutoff(text):
    """Parse a user-supplied SAME-DAY cleanup cutoff.

    The cutoff is never hard-coded: whatever valid clock time the user writes is
    used.  Supported examples include:
      删除6.00pm的废话 / 删除6.31pm前的对话 / 清除 5pm 前的废话 / 删除 17:35 前消息
      清除下午5点 / 删除下午5点30 / 清除下午5点半 / 删除晚上6点20的废话

    "删除/清除 + time + 废话" means "delete chatter before that time" even if
    the user omits the word "前".  This parser only returns a cutoff; the delete
    routine still protects EVERY message containing at least one digit.
    """
    t=(text or "").strip()
    if not t:
        return None
    # Normalize common full-width punctuation/spacing without changing digits.
    t=(t.replace("：", ":").replace("．", ".").replace("。", ".")
         .replace("ＡＭ", "AM").replace("ＰＭ", "PM"))

    # Require an explicit cleanup verb so ordinary accounting text can never
    # accidentally trigger deletion.
    m=re.match(r"^(清除|删除|刪除)\s*(.+?)\s*$", t, re.I)
    if not m:
        return None
    body=m.group(2).strip()

    # Remove only cleanup wording around the clock expression.  "前" is
    # optional because users naturally write e.g. 删除6.00pm的废话.
    body=re.sub(r"\s*(?:以前|之前|前)?\s*(?:的)?\s*(?:废话|廢話|闲聊|閒聊|聊天|对话|對話|消息|訊息)?\s*$", "", body, flags=re.I).strip()
    if not body:
        return None

    # Chinese clock forms: 下午5点, 下午5点30, 下午5点30分, 下午5点半.
    m=re.fullmatch(r"(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*(\d{1,2})\s*(?:点|點|时|時)\s*(?:(半)|(\d{1,2})\s*(?:分)?)?", body, re.I)
    if m:
        part=(m.group(1) or "")
        h=int(m.group(2))
        mi=30 if m.group(3) else int(m.group(4) or 0)
        if part in {"下午","傍晚","晚上"} and h<12:
            h+=12
        elif part in {"凌晨","早上","上午"} and h==12:
            h=0
        elif part=="中午" and 1 <= h < 12:
            h+=12
        return (h,mi) if 0<=h<=23 and 0<=mi<=59 else None

    # 12/24-hour forms: 5pm, 5.00pm, 5:30 PM, 17:35, 17.35.
    m=re.fullmatch(r"(\d{1,2})(?:\s*[:.]\s*(\d{1,2}))?\s*(am|pm)?", body, re.I)
    if not m:
        return None
    h=int(m.group(1)); mi=int(m.group(2) or 0); ap=(m.group(3) or "").lower()
    if ap:
        if not 1<=h<=12:
            return None
        if ap=="pm" and h<12:
            h+=12
        elif ap=="am" and h==12:
            h=0
    return (h,mi) if 0<=h<=23 and 0<=mi<=59 else None

async def _delete_company_chatter_before(update, context, hour, minute):
    chat=update.effective_chat
    actor=update.effective_user
    if not chat or int(chat.id) not in FIXED_CALC_CHAT_IDS:
        return False
    if not actor or not (int(actor.id)==int(OWNER_ID) or bot_admin_has(int(actor.id))):
        await update.effective_message.reply_text("⛔ 只有 Owner / Bot Admin 可以清理群消息。")
        return True
    now=datetime.now(TZ)
    cutoff=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    day_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    with db() as c:
        rows=c.execute("""SELECT message_id,has_digit,text_preview FROM calc_chat_message_log
            WHERE chat_id=? AND deleted=0 AND protected=0
              AND message_time>=? AND message_time<? ORDER BY message_id""",
            (int(chat.id),day_start.strftime("%Y-%m-%d %H:%M:%S"),
             cutoff.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
    deleted=0; failed=0; kept_numeric=0
    for r in rows:
        mid=int(r["message_id"])
        if mid==int(update.effective_message.message_id):
            continue
        preview=str(r["text_preview"] or "")
        # Final cleanup rule requested by the user:
        #   - ANY message containing am/pm is time-class chatter and is deletable,
        #     even if it also contains dates, RM amounts, codes, or other digits.
        #   - Without am/pm, ANY digit protects the message.
        # This is evaluated here (not only when logged) so already-recorded
        # messages immediately follow the newest rule after deployment.
        has_ampm=bool(re.search(r"(?i)(?:^|\b)\d{1,2}(?:[:.]\d{1,2})?\s*(?:am|pm)\b", preview))
        if int(r["has_digit"] or 0) and not has_ampm:
            kept_numeric+=1
            continue
        try:
            await context.bot.delete_message(chat_id=int(chat.id),message_id=mid)
            with db() as c:
                c.execute("UPDATE calc_chat_message_log SET deleted=1 WHERE chat_id=? AND message_id=?",
                          (int(chat.id),mid))
            deleted+=1
            await asyncio.sleep(0.06)
        except RetryAfter as e:
            await asyncio.sleep(float(getattr(e,"retry_after",1.0))+0.2)
            try:
                await context.bot.delete_message(chat_id=int(chat.id),message_id=mid)
                with db() as c:
                    c.execute("UPDATE calc_chat_message_log SET deleted=1 WHERE chat_id=? AND message_id=?",
                              (int(chat.id),mid))
                deleted+=1
            except Exception:
                failed+=1
        except Exception:
            failed+=1
    msg=(f"🧹 已清理 {cutoff:%H:%M} 前的对话\n"
         f"删除：{deleted} 条\n"
         f"数字保护：{kept_numeric} 条\n"
         "规则：有 am/pm = 时间类可删除；没有 am/pm 且有数字 = 保留。")
    if failed:
        msg+=f"\n未能删除：{failed} 条（权限/Telegram限制）"
    await update.effective_message.reply_text(msg)
    return True

def calc_balance():
    opening=D(calc_get('opening_balance','0'))
    cid=calc_scope_chat_id()
    with db() as c: rows=c.execute("SELECT kind,amount FROM calc_entries WHERE chat_id=? AND currency='RM'",(cid,)).fetchall()
    bal=opening
    for r in rows:
        a=D(r['amount']); bal += a if r['kind']=='income' else (-a if r['kind']=='expense' else D('0'))
    return money(bal)

def calc_balance_at(target_date):
    """True historical closing balance.

    Never back-fill today's/current balance into an earlier day.  A balance can
    only change from (1) the dated opening/fund-set baseline and (2) RM ledger
    entries whose accounting date is on/before target_date.  Moving an existing
    entry to another date only changes its reporting date; it never creates a
    second balance movement.
    """
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()

    opening = D(calc_get('opening_balance','0'))
    opening_date_s = calc_get('opening_balance_date','')
    if not opening_date_s:
        # One-time migration for old installs: anchor the existing baseline to
        # the first accounting day we actually have, instead of leaking it into
        # dates before any recorded company account activity.
        with db() as c:
            r = c.execute("SELECT MIN(COALESCE(record_date,substr(created_at,1,10))) d FROM calc_entries WHERE chat_id=? AND currency='RM'",(calc_scope_chat_id(),)).fetchone()
        opening_date_s = (r['d'] if r and r['d'] else datetime.now(TZ).date().isoformat())
        calc_set('opening_balance_date', opening_date_s)
    try:
        opening_date = datetime.strptime(opening_date_s, '%Y-%m-%d').date()
    except Exception:
        opening_date = datetime.now(TZ).date()

    bal = opening if target_date >= opening_date else D('0')
    with db() as c:
        rows = c.execute("""
            SELECT kind, amount
            FROM calc_entries
            WHERE chat_id=? AND currency='RM'
              AND COALESCE(record_date, substr(created_at,1,10)) <= ?
        """, (calc_scope_chat_id(), target_date.isoformat())).fetchall()
    for r in rows:
        a = D(r['amount'])
        if r['kind'] == 'income': bal += a
        elif r['kind'] == 'expense': bal -= a
    return money(bal)

def calc_period_bounds(kind, anchor=None):
    d=anchor or datetime.now(TZ).date()
    if kind=='day': s=d; e=d
    elif kind=='week': s=d-timedelta(days=d.weekday()); e=s+timedelta(days=6)
    elif kind=='month': s=d.replace(day=1); e=d.replace(day=calendar.monthrange(d.year,d.month)[1])
    else: s=d.replace(month=1,day=1); e=d.replace(month=12,day=31)
    return s,e

def _calc_funder_totals(start_date, end_date, pending_only=False):
    """Return L/W advance totals for this company only; advances do not affect balance until settlement."""
    sql="""SELECT UPPER(funder_tag) tag, amount FROM calc_funder_advances
           WHERE chat_id=? AND UPPER(funder_tag) IN ('L','W')
             AND record_date BETWEEN ? AND ?"""
    args=[calc_scope_chat_id(),start_date.isoformat(),end_date.isoformat()]
    if pending_only:
        sql += " AND settled=0"
    with db() as c:
        rows=c.execute(sql,args).fetchall()
    out={'L':D('0'),'W':D('0')}
    for r in rows:
        out[str(r['tag']).upper()] += D(r['amount'])
    return {k:money(v) for k,v in out.items()}

def _calc_funder_outstanding_totals(as_of_date=None):
    """Outstanding L/W carried forward until explicit settlement.

    As-of queries include rows already settled later, so historical /today can
    still show what was outstanding on that date.
    """
    d=as_of_date or datetime.now(TZ).date()
    cid=calc_scope_chat_id()
    with db() as c:
        rows=c.execute("""SELECT funder_tag, amount, settled, settled_at, record_date
            FROM calc_funder_advances
            WHERE chat_id=? AND UPPER(funder_tag) IN ('L','W')
              AND record_date<=?""", (cid,d.isoformat())).fetchall()
    out={'L':D('0'),'W':D('0')}
    for r in rows:
        include = not int(r['settled'] or 0)
        if not include and r['settled_at']:
            try:
                sd=datetime.fromisoformat(str(r['settled_at'])).astimezone(TZ).date()
                include = sd > d
            except Exception:
                include = False
        if include:
            out[str(r['funder_tag']).upper()] += D(r['amount'])
    return {k:money(v) for k,v in out.items()}


def _calc_ad_display_total(start_date, end_date):
    """Advertising total for display, including L/W advance-only rows.

    A row such as ``广告 4160 L`` keeps its original record_date and remains an
    L advance until settlement, but it must also contribute to the advertising
    category display.  Advance-only rows never change company balance here.
    Formal expenses later tagged L/W are excluded from the advance side via
    source_entry_id so the same money is never counted twice.
    """
    cid=calc_scope_chat_id()
    def is_ad(v):
        x=re.sub(r'\s+','',str(v or '')).casefold()
        return ('广告' in x) or ('廣告' in x) or ('ads' in x)
    total=D('0')
    with db() as c:
        formal=c.execute("""SELECT amount,category,label FROM calc_entries
            WHERE chat_id=? AND kind='expense' AND currency='RM'
              AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ?
              AND COALESCE(category,'')!='垫付结算'""",
            (cid,start_date.isoformat(),end_date.isoformat())).fetchall()
        advances=c.execute("""SELECT amount,label,raw_text FROM calc_funder_advances
            WHERE chat_id=? AND record_date BETWEEN ? AND ?
              AND source_entry_id IS NULL""",
            (cid,start_date.isoformat(),end_date.isoformat())).fetchall()
    for r in formal:
        if is_ad(r['category']) or is_ad(r['label']):
            total += D(r['amount'])
    for r in advances:
        if is_ad(r['label']) or is_ad(r['raw_text']):
            total += D(r['amount'])
    return money(total)


def _calc_funder_day_display_totals(start_date, end_date):
    """L/W totals for display without double-counting.

    Formal company expenses classified by Reply L/W are read directly from
    calc_entries.funder_tag. Advance-only rows (no source_entry_id) are read
    from calc_funder_advances. Settlement entries themselves are excluded so
    settling an advance never looks like a new advance on the settlement day.
    """
    out={'L':D('0'),'W':D('0')}
    cid=calc_scope_chat_id()
    with db() as c:
        formal=c.execute("""SELECT UPPER(funder_tag) tag, amount
            FROM calc_entries
            WHERE chat_id=? AND kind='expense' AND currency='RM'
              AND UPPER(COALESCE(funder_tag,'')) IN ('L','W')
              AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ?
              AND COALESCE(category,'')!='垫付结算'""",
            (cid,start_date.isoformat(),end_date.isoformat())).fetchall()
        advances=c.execute("""SELECT UPPER(funder_tag) tag, amount
            FROM calc_funder_advances
            WHERE chat_id=? AND UPPER(funder_tag) IN ('L','W')
              AND record_date BETWEEN ? AND ?
              AND source_entry_id IS NULL""",
            (cid,start_date.isoformat(),end_date.isoformat())).fetchall()
    for r in list(formal)+list(advances):
        out[str(r['tag']).upper()] += D(r['amount'])
    return {k:money(v) for k,v in out.items()}

def _calc_funder_pending_rows(tag, start_date, end_date):
    with db() as c:
        return c.execute("""SELECT * FROM calc_funder_advances
            WHERE chat_id=? AND UPPER(funder_tag)=? AND record_date BETWEEN ? AND ?
            ORDER BY record_date,created_at,id""",
            (calc_scope_chat_id(),tag.upper(),start_date.isoformat(),end_date.isoformat())).fetchall()

def calc_funder_report(tag, anchor=None, kind='month'):
    tag=(tag or '').upper()
    if tag not in {'L','W'}: return '❌ 只支持 L费用 / W费用'
    s,e=calc_period_bounds(kind,anchor or datetime.now(TZ).date())
    rows=_calc_funder_pending_rows(tag,s,e)
    out=[f'💼 {calc_company_title()}｜{tag}垫付',f'📅 {s.strftime("%d/%m/%Y")} - {e.strftime("%d/%m/%Y")}']
    if not rows:
        out += ['', '暂无记录。', '', f'{tag} 垫付 TTL：RM0.00']
        return '\n'.join(out)
    total=D('0'); pending=D('0'); out += ['']
    for i,r in enumerate(rows,1):
        a=D(r['amount']); total+=a
        if not int(r['settled'] or 0): pending+=a
        label=r['label'] or '未注明'
        mark='✅ 已结算' if int(r['settled'] or 0) else '⏳ 待结算'
        try: d=datetime.strptime(r['record_date'],'%Y-%m-%d').strftime('%d/%m/%Y')
        except: d=r['record_date']
        out.append(f'{i}. RM{money(a):,.2f}｜{label}｜{d}｜{mark}')
    out += ['', f'{tag} 垫付 TTL：RM{money(total):,.2f}', f'待结算：RM{money(pending):,.2f}', f'共 {len(rows)} 笔']
    return '\n'.join(out)

def _parse_lw_advance_block(text):
    """Parse L/W advance text. No leading +/- means advance only, no company balance impact."""
    raw=(text or '').strip()
    if not raw: return []
    # Explicit +/- belongs to the normal ledger, not the advance-only parser.
    if re.match(r'^\s*[+-]\s*\d', raw):
        return []
    today=datetime.now(TZ).date()
    current_date=today
    out=[]
    lines=[x.strip() for x in raw.splitlines() if x.strip()]
    # Single-line L费用/W费用 form.
    m=re.fullmatch(r'([LWlw])\s*(?:费用|費用)\s*(?:RM|MYR)?\s*([\d,]+(?:\.\d+)?)\s*(.*?)\s*', raw, re.I)
    if m:
        out.append((m.group(1).upper(),money(m.group(2).replace(',','')),clean_label(m.group(3).strip()) if m.group(3).strip() else '未注明',today.isoformat(),raw))
        return out
    for line in lines:
        # Date-only line, e.g. 05/09 or 05/09/2026.
        dm=re.fullmatch(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', line)
        if dm:
            day,mon=int(dm.group(1)),int(dm.group(2)); yr=dm.group(3)
            year=today.year if not yr else int(yr) + (2000 if len(yr)==2 else 0)
            try: current_date=datetime(year,mon,day).date()
            except ValueError: pass
            continue
        # Date may be at the front OR end of the line. Supported examples:
        #   17-09-2026 topup 30 L
        #   topup 30 L 17/09/26
        #   广告4160 L 11-09-2026
        work=line
        date_m=re.search(r'(?<!\d)(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?!\d)', work)
        line_date=current_date
        if date_m:
            day,mon=int(date_m.group(1)),int(date_m.group(2)); yr=date_m.group(3)
            year=today.year if not yr else int(yr) + (2000 if len(yr)==2 else 0)
            try:
                line_date=datetime(year,mon,day).date()
                current_date=line_date
                work=(work[:date_m.start()]+' '+work[date_m.end():]).strip()
            except ValueError:
                pass
        lm=re.fullmatch(r'(.+?)\s+(?:RM|MYR)?\s*([\d,]+(?:\.\d+)?)\s*([LWlw])\s*', work, re.I)
        if not lm:
            # Also allow compact forms like 广告4160 L / 车费80 W.
            lm=re.fullmatch(r'(.+?)(?:RM|MYR)?\s*([\d,]+(?:\.\d+)?)\s*([LWlw])\s*', work, re.I)
        if not lm: continue
        label=clean_label((lm.group(1) or '').strip()) or '未注明'
        amt=money(lm.group(2).replace(',','')); tag=lm.group(3).upper()
        out.append((tag,amt,label,line_date.isoformat(),line))
    return out

def _record_lw_advances(update, rows):
    ids=[]
    with db() as c:
        for tag,amt,label,recdate,raw in rows:
            cur=c.execute("""INSERT INTO calc_funder_advances
                (created_at,chat_id,user_id,funder_tag,amount,label,raw_text,record_date,settled)
                VALUES(?,?,?,?,?,?,?,?,0)""",
                (now_iso(),update.effective_chat.id,update.effective_user.id,tag,str(money(amt)),label,raw,recdate))
            ids.append(cur.lastrowid)
    return ids

def _funder_month_total(tag, anchor_date=None, pending_only=False):
    d=anchor_date or datetime.now(TZ).date()
    s,e=calc_period_bounds('month',d)
    totals=_calc_funder_totals(s,e,pending_only=pending_only)
    return money(totals.get((tag or '').upper(),D('0')))

def _parse_lw_reply_block(text, force_tag):
    """Parse a replied historical expense block and force every money row to L/W.

    This is intentionally user-directed: Replying the message with L or W is the
    explicit classification action, so old rows do not need to be retyped.
    """
    raw=(text or '').strip(); tag=(force_tag or '').upper()
    if not raw or tag not in {'L','W'}: return []
    today=datetime.now(TZ).date(); current_date=today; out=[]
    lines=[x.strip() for x in raw.splitlines() if x.strip()]
    for line_no,line in enumerate(lines,1):
        dm=re.fullmatch(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?',line)
        if dm:
            day,mon=int(dm.group(1)),int(dm.group(2)); yr=dm.group(3)
            year=today.year if not yr else int(yr)+(2000 if len(yr)==2 else 0)
            try: current_date=datetime(year,mon,day).date()
            except ValueError: pass
            continue
        work=line
        # Optional date prefix on the same line.
        dm=re.match(r'^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s+(.+)$',work)
        if dm:
            day,mon=int(dm.group(1)),int(dm.group(2)); yr=dm.group(3)
            year=today.year if not yr else int(yr)+(2000 if len(yr)==2 else 0)
            try: current_date=datetime(year,mon,day).date()
            except ValueError: pass
            work=dm.group(4).strip()
        # Existing trailing L/W is just an old/manual classification marker;
        # the Reply L/W intentionally overrides it.
        work=re.sub(r'\s+[LWlw]\s*$','',work).strip()
        # Find the last money-looking token; everything before it is the label.
        ms=list(re.finditer(r'(?i)(?:RM|MYR)?\s*([0-9][0-9,]*(?:\.\d+)?)',work))
        if not ms: continue
        m=ms[-1]
        try: amt=money(m.group(1).replace(',',''))
        except Exception: continue
        label=clean_label(work[:m.start()].strip(' -—_:：')) or '未注明'
        if amt < 0: amt=-amt
        if amt == 0: continue
        out.append((tag,amt,label,current_date.isoformat(),line,line_no))
    return out

def _record_or_reclassify_lw_reply(update, reply_msg, tag):
    """Reply one old/new message with L/W: classify all its expense rows once."""
    chat_id=int(update.effective_chat.id); tag=tag.upper(); src_mid=int(reply_msg.message_id)
    with db() as c:
        existing=c.execute('''SELECT id FROM calc_funder_advances
            WHERE chat_id=? AND source_message_id=? ORDER BY source_line_no,id''',(chat_id,src_mid)).fetchall()
        if existing:
            c.execute('UPDATE calc_funder_advances SET funder_tag=? WHERE chat_id=? AND source_message_id=?',(tag,chat_id,src_mid))
            return len(existing), True, None

    # If the replied message is already a formal +/- ledger entry, only add/change
    # the funder attribution. Do not deduct company balance again.
    rr=calc_receipt_entry(update)
    if rr:
        eid=int(rr['entry_id'])
        with db() as c:
            ent=c.execute('SELECT * FROM calc_entries WHERE id=? AND chat_id=?',(eid,chat_id)).fetchone()
            if ent:
                c.execute('UPDATE calc_entries SET funder_tag=? WHERE id=?',(tag,eid))
                prev=c.execute('SELECT id FROM calc_funder_advances WHERE source_entry_id=?',(eid,)).fetchone()
                recdate=ent['record_date'] or str(ent['created_at'])[:10]
                if prev:
                    c.execute('UPDATE calc_funder_advances SET funder_tag=?,settled=0,settlement_entry_id=NULL,settled_at=NULL WHERE id=?',(tag,int(prev['id'])))
                else:
                    c.execute('''INSERT INTO calc_funder_advances
                        (created_at,chat_id,user_id,funder_tag,amount,label,raw_text,record_date,settled,settlement_entry_id,source_entry_id,source_message_id,source_line_no)
                        VALUES(?,?,?,?,?,?,?,?,0,NULL,?,?,1)''',
                        (now_iso(),chat_id,ent['user_id'],tag,str(money(ent['amount'])),ent['label'] or ent['category'] or '未注明',ent['raw_text'],recdate,eid,src_mid))
                return 1, False, 'formal'

    rtext=(reply_msg.text or reply_msg.caption or '').strip()
    rows=_parse_lw_reply_block(rtext,tag)
    if not rows: return 0,False,None
    with db() as c:
        for rtag,amt,label,recdate,raw,line_no in rows:
            old=c.execute('''SELECT id FROM calc_funder_advances
                WHERE chat_id=? AND source_message_id=? AND source_line_no=?''',(chat_id,src_mid,line_no)).fetchone()
            if old:
                c.execute('''UPDATE calc_funder_advances SET funder_tag=?,amount=?,label=?,raw_text=?,record_date=?
                    WHERE id=?''',(tag,str(money(amt)),label,raw,recdate,int(old['id'])))
            else:
                c.execute('''INSERT INTO calc_funder_advances
                    (created_at,chat_id,user_id,funder_tag,amount,label,raw_text,record_date,settled,source_message_id,source_line_no)
                    VALUES(?,?,?,?,?,?,?,?,0,?,?)''',
                    (now_iso(),chat_id,getattr(getattr(reply_msg,'from_user',None),'id',None),tag,str(money(amt)),label,raw,recdate,src_mid,line_no))
    return len(rows),False,'advance'

def _settle_lw_advances(tag, anchor=None):
    """Settle every outstanding L/W amount up to the chosen day.

    Advance-only rows become a company expense exactly once. Formal expenses
    already tagged L/W are only marked settled because they already affected
    company balance when originally posted.
    """
    tag=(tag or '').upper(); cutoff=anchor or datetime.now(TZ).date()
    with db() as c:
        rows=c.execute("""SELECT * FROM calc_funder_advances
            WHERE chat_id=? AND UPPER(funder_tag)=? AND settled=0 AND record_date<=?
            ORDER BY record_date,id""",
            (calc_scope_chat_id(),tag,cutoff.isoformat())).fetchall()
        if not rows: return None,0,D('0')
        total=sum((D(r['amount']) for r in rows),D('0'))
        advance_only=[r for r in rows if r['source_entry_id'] is None]
        expense_total=sum((D(r['amount']) for r in advance_only),D('0'))
        eid=None
        if expense_total>0:
            label=f'{tag}垫付结算 {cutoff:%Y-%m}'
            cur=c.execute("""INSERT INTO calc_entries
                (created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,funder_tag)
                VALUES(?,?,?,?,?,'RM',?,?,?,1,?,?)""",
                (now_iso(),calc_scope_chat_id(),None,'expense',str(money(expense_total)),'垫付结算',label,label,cutoff.isoformat(),tag))
            eid=cur.lastrowid
        q=','.join('?' for _ in rows)
        c.execute(f"UPDATE calc_funder_advances SET settled=1,settlement_entry_id=?,settled_at=? WHERE id IN ({q})",
                  [eid,now_iso()]+[r['id'] for r in rows])
    return eid,len(rows),money(total)


def calc_summary(kind='day', anchor=None):
    s,e=calc_period_bounds(kind,anchor)
    with db() as c:
        cid=calc_scope_chat_id()
        rows=c.execute("SELECT * FROM calc_entries WHERE chat_id=? AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ? ORDER BY id",(cid,s.isoformat(),e.isoformat())).fetchall()
        staff=c.execute('SELECT * FROM calc_staff_reports_v2 WHERE chat_id=? AND report_date BETWEEN ? AND ? ORDER BY staff',(cid,s.isoformat(),e.isoformat())).fetchall()
        card_count=c.execute("SELECT COUNT(*) n FROM calc_cardfee_pending WHERE chat_id=? AND status='posted' AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ?",(cid,s.isoformat(),e.isoformat())).fetchone()['n']
    inc=Decimal('0'); exp=Decimal('0'); u=Decimal('0'); inc_cat={}; exp_cat={}
    funder_totals=_calc_funder_totals(s,e)
    for r in rows:
        a=D(r['amount']); label=r['label'] or r['category'] or '其他'
        if r['currency']=='U' and not r['settled']:
            u+=a; continue
        if r['currency']!='RM': continue
        label_key=re.sub(r'\s+','',str(label)).casefold()
        if r['kind']=='income' and label_key in {'公司桶','公司资金','公司資金','companyfund','companybucket','openingbalance'}:
            continue
        if r['kind']=='income': inc+=a; inc_cat[label]=inc_cat.get(label,D('0'))+a
        elif r['kind']=='expense': exp+=a; exp_cat[label]=exp_cat.get(label,D('0'))+a
    title={'day':'日报','week':'周报','month':'月报','year':'年报'}[kind]
    if kind=='day':
        bal=(calc_balance() if s == datetime.now(TZ).date() else calc_balance_at(s))
        # /today is a current monthly snapshot for advertising too: keep the
        # original transaction date, but carry this month's categorized ad
        # amounts into today's display. L/W advance-only ad rows are display
        # classification only and never deduct balance here.
        month_start=s.replace(day=1)
        ad_rm=_calc_ad_display_total(month_start,s)
        # L/W is a carried-forward outstanding balance, not a daily counter.
        # It keeps accumulating across dates until the user explicitly settles.
        outstanding=_calc_funder_outstanding_totals(s)
        day_l=money(outstanding.get('L',D('0'))); day_w=money(outstanding.get('W',D('0')))
        pending_l=day_l; pending_w=day_w
        out=[f'📊 {calc_company_title()}｜今日账单',f'📅 {s.strftime("%d/%m/%Y")}', '',
             f'📥 收入：RM{money(inc):,.2f}',f'📤 支出：RM{money(exp):,.2f}',
             f'📈 净额：{("+" if inc-exp>=0 else "-")}RM{abs(money(inc-exp)):,.2f}',f'🏦 结余：RM{bal:,.2f}', '',
             f'💼 L垫付：RM{day_l:,.2f}',f'💼 W垫付：RM{day_w:,.2f}',f'⏳ 待结算：RM{money(pending_l+pending_w):,.2f}', '',
             f'💳 卡费：{int(card_count)} 笔',f'📣 广告费用：RM{money(ad_rm):,.2f}｜广告U：{money(u):,.2f} U']
        return '\n'.join(out)
    out=[f'📊 {calc_company_title()}｜公司账{title}',f'📅 {s.strftime("%d/%m/%Y")} - {e.strftime("%d/%m/%Y")}', '',f'📥 收入：RM{money(inc):,.2f}',f'📤 支出：RM{money(exp):,.2f}',f'📈 净额：RM{money(inc-exp):,.2f}',f'🏦 期末结余：RM{calc_balance_at(e):,.2f}',f'📢 广告 U 待结算：{money(u):,.2f} U']
    if inc_cat:
        out += ['', '收入分类：']+[f'• {k}：RM{money(v):,.2f}' for k,v in inc_cat.items()]
    if exp_cat:
        out += ['', '支出分类：']+[f'• {k}：RM{money(v):,.2f}' for k,v in exp_cat.items()]
    outstanding=_calc_funder_outstanding_totals(e)
    if funder_totals['L'] or funder_totals['W'] or outstanding['L'] or outstanding['W']:
        out += ['', '💼 出资/垫付汇总：',
                f'• 本期 L：RM{money(funder_totals["L"]):,.2f}',
                f'• 本期 W：RM{money(funder_totals["W"]):,.2f}',
                f'• 当前未结算 L：RM{money(outstanding["L"]):,.2f}',
                f'• 当前未结算 W：RM{money(outstanding["W"]):,.2f}',
                f'• 当前待结算：RM{money(outstanding["L"]+outstanding["W"]):,.2f}']
    if staff:
        out += ['', '👥 员工结算：']+[f"• {r['staff']}：Sale RM{D(r['sale']):,.2f}｜COM RM{D(r['com20']):,.2f}｜{r['status']}" for r in staff]
    return '\n'.join(out)

def calc_simple_summary(kind='day', anchor=None):
    s,e=calc_period_bounds(kind,anchor)
    start=datetime.combine(s,time.min,tzinfo=TZ).isoformat(); end=datetime.combine(e,time.max,tzinfo=TZ).isoformat()
    with db() as c:
        cid=calc_scope_chat_id()
        rows=c.execute("SELECT * FROM calc_entries WHERE chat_id=? AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ? ORDER BY id",(cid,s.isoformat(),e.isoformat())).fetchall()
    inc=Decimal('0'); exp=Decimal('0'); u=Decimal('0'); cats={}; ad_rm=Decimal('0')
    for r in rows:
        a=D(r['amount'])
        if r['currency']=='U' and not r['settled']:
            u+=a; continue
        if r['currency']!='RM': continue
        key=r['category'] or r['label'] or '其他'
        key_norm=re.sub(r'\s+','',str(key)).casefold()
        if r['kind']=='income' and key_norm in {'公司桶','公司资金','公司資金','companyfund','companybucket','openingbalance'}:
            continue
        if r['kind']=='income': inc+=a
        elif r['kind']=='expense':
            exp+=a
            if key_norm in {'广告','廣告','ads','广告u结算','廣告u結算'}:
                ad_rm+=a
        signed=a if r['kind']=='income' else -a
        cats[key]=cats.get(key,D('0'))+signed
    # Category display also includes advance-only L/W rows (e.g. 广告 4160 L)
    # without posting them to company expenses. Exact original record_date is
    # retained in the advance table.
    ad_rm=_calc_ad_display_total(s,e)
    title={'day':'今日','week':'本周','month':'本月','year':'今年'}[kind]
    out=[f'📊 {calc_company_title()}｜简易账｜{title}',f'📅 {s.strftime("%d/%m/%Y")} - {e.strftime("%d/%m/%Y")}', '',
         f'📥 收入 TTL：RM{money(inc):,.2f}',f'📤 支出 TTL：RM{money(exp):,.2f}',
         f'📈 净额：{("+" if inc-exp>=0 else "-")}RM{abs(money(inc-exp)):,.2f}',f'🏦 期末结余：RM{calc_balance_at(e):,.2f}']
    out.append(f'📣 广告费用：RM{money(ad_rm):,.2f}｜广告U：{money(u):,.2f} U')
    # L/W display totals include BOTH formal expenses later tagged by Reply L/W
    # and advance-only records. Pending remains advance-only + unsettled.
    ft_all=_calc_funder_day_display_totals(s,e)
    ft_pending=_calc_funder_totals(s,e,pending_only=True)
    if ft_all['L'] or ft_all['W'] or ft_pending['L'] or ft_pending['W']:
        out += ['', '💼 L/W 出资/垫付：',
                f'• L：RM{money(ft_all["L"]):,.2f}',
                f'• W：RM{money(ft_all["W"]):,.2f}',
                f'• 待结算：RM{money(ft_pending["L"]+ft_pending["W"]):,.2f}']
    if cats:
        out += ['','分类：']
        for k,v in cats.items(): out.append(f'• {k}：{("+" if v>=0 else "-")}RM{abs(money(v)):,.2f}')
    return '\n'.join(out)

def _calc_entry_display_time(row):
    """Accounting date + original posting time in Malaysia time."""
    try:
        ct=datetime.fromisoformat(row['created_at']).astimezone(TZ)
    except Exception:
        try:
            ct=datetime.fromisoformat(str(row['created_at']))
            if ct.tzinfo is None: ct=ct.replace(tzinfo=TZ)
            else: ct=ct.astimezone(TZ)
        except Exception:
            ct=datetime.now(TZ)
    rd=(row['record_date'] if 'record_date' in row.keys() and row['record_date'] else ct.date().isoformat())
    try: dshow=datetime.strptime(rd,'%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception: dshow=str(rd)
    return f"{dshow} {ct.strftime('%H:%M')}"


def _calc_card_code_lines(start_date, end_date):
    """Return each posted card code with its amount and accounting date/time."""
    with db() as c:
        rows=c.execute("""
            SELECT id,created_at,record_date,card_details,raw_text
            FROM calc_cardfee_pending
            WHERE chat_id=? AND status='posted'
              AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ?
            ORDER BY COALESCE(record_date,substr(created_at,1,10)),created_at,id
        """,(calc_scope_chat_id(),start_date.isoformat(),end_date.isoformat())).fetchall()
    out=[]
    for r in rows:
        details=[]
        try:
            details=json.loads(r['card_details'] or '[]')
        except Exception:
            details=[]
        # Backward compatibility for old batches that pre-date card_details.
        if not details:
            for line in (r['raw_text'] or '').splitlines():
                d=_parse_card_code_money_line(line)
                if d and d.get('amount') is not None:
                    details.append({'code':d['code'],'amount':str(d['amount'])})
        try:
            ct=datetime.fromisoformat(r['created_at']).astimezone(TZ)
        except Exception:
            ct=datetime.now(TZ)
        rd=r['record_date'] or ct.date().isoformat()
        try: when=f"{datetime.strptime(rd,'%Y-%m-%d').strftime('%d/%m/%Y')} {ct.strftime('%H:%M')}"
        except Exception: when=f"{rd} {ct.strftime('%H:%M')}"
        for d in details:
            code=str(d.get('code') or '').strip()
            try: amt=money(d.get('amount') or 0)
            except Exception: amt=money(0)
            if code:
                out.append((code,amt,when))
    return out


def _calc_compact_entry_note(row):
    """Keep user-written notes readable without dumping structural card blocks."""
    raw=(row['raw_text'] or '').strip()
    if not raw:
        return ''
    label=(row['label'] or row['category'] or '').strip()
    is_card=(row['category'] in ('卡费','卡费 Fee') or label in ('卡费 Gross','卡费 Fee'))
    if not is_card:
        # Generic income/expense: what the user typed is the audit note.
        lines=[x.strip() for x in raw.splitlines() if x.strip()]
        text=' / '.join(lines)
        return text[:500] + ('...' if len(text)>500 else '')

    notes=[]
    for line in (x.strip() for x in raw.splitlines() if x.strip()):
        if re.fullmatch(r'[-—_=\s]+',line):
            continue
        if parse_calc_date_text(line):
            continue
        if re.search(r'(?i)卡费\s*(?:RM\s*)?[\d,]+(?:\.\d+)?\s*[xX×]\s*\d+',line):
            continue
        if re.match(r'(?i)^\s*T(?:otal)?\s*[:：]',line):
            continue
        d=_parse_card_code_money_line(line)
        if d:
            note=(d.get('note') or '').strip(' ()（）')
            if note and note not in notes:
                notes.append(note)
            continue
        if _calc_annotated_money_expression(line) is not None:
            if line not in notes: notes.append(line)
    return '；'.join(notes)[:300]


def calc_full_ledger(kind='day', anchor=None):
    s,e=calc_period_bounds(kind,anchor)
    with db() as c:
        cid=calc_scope_chat_id()
        rows=c.execute("SELECT * FROM calc_entries WHERE chat_id=? AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ? ORDER BY COALESCE(record_date,substr(created_at,1,10)),created_at,id",(cid,s.isoformat(),e.isoformat())).fetchall()
    inc=Decimal('0'); exp=Decimal('0'); u=Decimal('0')
    date_text=s.strftime('%d/%m/%Y') if s==e else f'{s.strftime("%d/%m/%Y")} - {e.strftime("%d/%m/%Y")}'
    out=[f'📒 {calc_company_title()}｜详细总账',f'📅 {date_text}']

    code_lines=_calc_card_code_lines(s,e)
    if code_lines:
        out += ['', f'【代号汇总】共 {len(code_lines)} 个']
        for i,(code,amt,when) in enumerate(code_lines,1):
            out.append(f'{i}. {code}｜RM{amt:,.2f}｜{when}')

    if rows:
        out += ['', '【明细】']
    else:
        out += ['', '暂无账目记录。']

    seq=0
    for r in rows:
        a=D(r['amount']); cur=r['currency']; label=r['label'] or r['category'] or '其他'
        tm=_calc_entry_display_time(r)
        sign='+' if r['kind']=='income' else '-'
        label_key=re.sub(r'\s+','',str(label)).casefold()
        is_fund=(r['kind']=='income' and label_key in {'公司桶','公司资金','公司資金','companyfund','companybucket','openingbalance'})
        seq += 1
        if cur=='RM':
            if r['kind']=='income' and not is_fund: inc+=a
            elif r['kind']=='expense': exp+=a
            icon='📥' if r['kind']=='income' else '📤'
            kind_text='公司资金' if is_fund else ('收入' if r['kind']=='income' else '支出')
            funder=(str(r['funder_tag']).upper() if 'funder_tag' in r.keys() and r['funder_tag'] else '')
            funder_text=f'｜{funder}费用' if funder in {'L','W'} else ''
            out.append(f'{seq}. {icon} {sign}RM{money(a):,.2f}｜{label}｜{kind_text}{funder_text}')
            out.append(f'   📅 {tm}')
        elif cur=='U':
            if not r['settled']: u+=a
            state='已结算' if r['settled'] else '待结算'
            out.append(f'{seq}. 📢 {sign}{money(a):,.2f} U｜{label}｜{state}')
            out.append(f'   📅 {tm}')
        note=_calc_compact_entry_note(r)
        if note:
            out.append(f'   📝 {note}')
        out.append('')

    # Advance-only L/W rows do not live in calc_entries, so list them here.
    with db() as c:
        adv_rows=c.execute("""SELECT * FROM calc_funder_advances
            WHERE chat_id=? AND UPPER(funder_tag) IN ('L','W')
              AND record_date BETWEEN ? AND ? AND source_entry_id IS NULL
            ORDER BY record_date,created_at,id""",
            (calc_scope_chat_id(),s.isoformat(),e.isoformat())).fetchall()
    if adv_rows:
        out += ['', '【L/W 垫付明细】']
        for i,r in enumerate(adv_rows,1):
            try:
                d=datetime.strptime(r['record_date'],'%Y-%m-%d').strftime('%d/%m/%Y')
            except Exception:
                d=r['record_date'] or ''
            mark='✅ 已结算' if int(r['settled'] or 0) else '⏳ 待结算'
            out.append(f"{i}. {str(r['funder_tag']).upper()}｜RM{money(r['amount']):,.2f}｜{r['label'] or '未注明'}｜{d}｜{mark}")

    ft_all=_calc_funder_day_display_totals(s,e)
    outstanding=_calc_funder_outstanding_totals(e)
    if ft_all['L'] or ft_all['W'] or outstanding['L'] or outstanding['W']:
        out += ['', '【L/W 汇总】',
                f"本期 L：RM{money(ft_all['L']):,.2f}",
                f"本期 W：RM{money(ft_all['W']):,.2f}",
                f"未结算 L：RM{money(outstanding['L']):,.2f}",
                f"未结算 W：RM{money(outstanding['W']):,.2f}",
                f"待结算：RM{money(outstanding['L']+outstanding['W']):,.2f}"]

    out += ['【汇总】',f'📥 收入：RM{money(inc):,.2f}',f'📤 支出：RM{money(exp):,.2f}',
            f'📈 净额：{("+" if inc-exp>=0 else "-")}RM{abs(money(inc-exp)):,.2f}',f'🏦 期末结余：RM{calc_balance_at(e):,.2f}']
    if u:
        out.append(f'📢 广告 U 待结算：{money(u):,.2f} U')
    return '\n'.join(out)

def _calc_extract_query_date(text):
    """Extract one supported accounting date from a free-text ledger query."""
    text=(text or '').strip()
    pats=[r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b',r'\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b']
    for pat in pats:
        m=re.search(pat,text)
        if not m: continue
        d=parse_calc_date_text(m.group(0))
        if d:
            rest=(text[:m.start()]+' '+text[m.end():]).strip()
            return d,re.sub(r'\s+',' ',rest)
    return None,text


def calc_ledger_search(query, limit=120):
    """Search this company's ledger using date/code/amount/original note text."""
    rawq=(query or '').strip()
    qdate,term=_calc_extract_query_date(rawq)
    term=term.strip()
    term_fold=term.casefold()
    with db() as c:
        sql="SELECT * FROM calc_entries WHERE chat_id=?"
        params=[calc_scope_chat_id()]
        if qdate:
            sql += " AND COALESCE(record_date,substr(created_at,1,10))=?"
            params.append(qdate.isoformat())
        sql += " ORDER BY COALESCE(record_date,substr(created_at,1,10)) DESC, created_at DESC, id DESC LIMIT 500"
        rows=c.execute(sql,params).fetchall()
    matched=[]
    for r in rows:
        if term_fold:
            hay=' | '.join(str(r[k] or '') for k in ('amount','currency','category','label','raw_text','record_date','created_at','batch_id','funder_tag') if k in r.keys()).casefold()
            if 'funder_tag' in r.keys() and r['funder_tag']:
                hay += ' | ' + str(r['funder_tag']).casefold() + '费用'
            # numeric amount query: allow 1,200 / 1200 / RM1200 to hit the same amount
            compact_q=re.sub(r'(?i)[^0-9.]','',term)
            compact_h=re.sub(r'(?i)[^0-9.]','',hay)
            if term_fold not in hay and not (compact_q and compact_q in compact_h):
                continue
        matched.append(r)
        if len(matched)>=limit: break
    title=f'🔎 {calc_company_title()}｜查询：{rawq or "全部"}'
    if qdate: title += f'\n📅 {qdate.strftime("%d/%m/%Y")}'
    out=[title,'━━━━━━━━━━']
    if not matched:
        out.append('没有找到对应账目。')
        return '\n'.join(out)
    inc=D('0'); exp=D('0')
    for i,r in enumerate(matched,1):
        a=D(r['amount']); sign='+' if r['kind']=='income' else '-'
        label=r['label'] or r['category'] or '其他'
        kind_text='收入' if r['kind']=='income' else '支出'
        if r['currency']=='RM':
            if r['kind']=='income': inc+=a
            elif r['kind']=='expense': exp+=a
            money_text=f'{sign}RM{money(a):,.2f}'
        else:
            money_text=f'{sign}{money(a):,.2f}{r["currency"]}'
        funder=(str(r['funder_tag']).upper() if 'funder_tag' in r.keys() and r['funder_tag'] else '')
        funder_text=f'｜{funder}费用' if funder in {'L','W'} else ''
        out.append(f'{i}. {label}｜{money_text}｜{kind_text}{funder_text}｜{_calc_entry_display_time(r)}')
        raw=(r['raw_text'] or '').strip()
        if raw:
            brief=' / '.join(x.strip() for x in raw.splitlines() if x.strip())
            if len(brief)>280: brief=brief[:277]+'...'
            if brief.casefold()!=str(label).strip().casefold(): out.append(f'   原文：{brief}')
        out.append('')
    out += ['━━━━━━━━━━',f'找到：{len(matched)} 笔',f'收入：RM{money(inc):,.2f}',f'支出：RM{money(exp):,.2f}']
    if len(matched)>=limit: out.append(f'⚠️ 结果较多，仅显示最近 {limit} 笔。')
    return '\n'.join(out)

def parse_anchor(arg, kind):
    if not arg: return datetime.now(TZ).date()
    try:
        if kind=='month': return datetime.strptime(arg,'%Y-%m').date()
        if kind=='year': return datetime.strptime(arg,'%Y').date()
        # Day/week lookups accept both ISO and familiar MY formats.
        return parse_calc_date_text(arg)
    except: return None

async def calculator_here_cmd(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text('⛔ 只有 Owner 可以绑定公司账群'); return
    if update.effective_chat.type == 'private':
        await update.effective_message.reply_text('❌ 请在公司账群使用 /calculator_here'); return
    calc_init_db()
    cid=int(update.effective_chat.id)
    if cid not in FIXED_CALC_CHAT_IDS:
        await update.effective_message.reply_text('❌ 这个群不是已确认的公司账群，未进行绑定。')
        return
    _calc_scope_chat.set(cid)
    # Keep config isolated: main uses the legacy main namespace; secondary uses
    # chat:-1004444989940:* via calc_set(). Never re-point the main namespace.
    if cid==SECONDARY_CALC_CHAT_ID:
        calc_set('title',update.effective_chat.title or SECONDARY_CALC_NAME)
    else:
        _raw_calc_set('chat_id',VOUCHER_CALC_CHAT_ID)
        _raw_calc_set('title',update.effective_chat.title or VOUCHER_CALC_NAME)
    await update.effective_message.reply_text(f'✅ {calc_company_title(cid)} 公司账已绑定\nGroup ID：{cid}\n\n账本与其他公司完全分开。')

async def fund_set_cmd(update, context):
    if not is_calc_chat(update): return
    if update.effective_user.id != OWNER_ID: await update.effective_message.reply_text('⛔ 只有 Owner 可以设置资金'); raise ApplicationHandlerStop
    if not context.args:
        await update.effective_message.reply_text(f'🏦 当前公司资金：RM{calc_balance():,.2f}\n用法：/fund_set 50000'); raise ApplicationHandlerStop
    try: target=money(context.args[0].replace(',',''))
    except: await update.effective_message.reply_text('❌ 金额格式错误'); raise ApplicationHandlerStop
    # set opening so computed current balance becomes target
    current=calc_balance(); opening=D(calc_get('opening_balance','0')); calc_set('opening_balance', money(opening + target-current)); calc_set('opening_balance_date', datetime.now(TZ).date().isoformat())
    await update.effective_message.reply_text(f'✅ 公司资金已设置：RM{calc_balance():,.2f}')
    raise ApplicationHandlerStop

async def calc_balance_cmd(update, context):
    if not is_calc_chat(update): return
    with db() as c: r=c.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) s FROM calc_entries WHERE chat_id=? AND currency='U' AND settled=0",(calc_scope_chat_id(),)).fetchone()
    await update.effective_message.reply_text(f'🏦 公司资金：RM{calc_balance():,.2f}\n📢 广告 U 待结算：{money(r["s"]):,.2f} U')
    raise ApplicationHandlerStop

async def calc_report_cmd(update, context, kind):
    if not is_calc_chat(update): return
    anchor=parse_anchor(context.args[0] if context.args else None,kind)
    if not anchor:
        await update.effective_message.reply_text('❌ 日期格式错误'); raise ApplicationHandlerStop
    await update.effective_message.reply_text(calc_summary(kind,anchor)); raise ApplicationHandlerStop
async def calc_today_cmd(u,c): await calc_report_cmd(u,c,'day')
async def calc_summary_cmd(update, context):
    if not is_calc_chat(update): return
    arg=' '.join(context.args).strip() if getattr(context,'args',None) else ''
    anchor=None
    if not arg:
        anchor=datetime.now(TZ).date()
    else:
        try:
            anchor=datetime.strptime(arg,'%Y-%m').date()
        except Exception:
            anchor=parse_anchor(arg,'day')
    if not anchor:
        await update.effective_message.reply_text('❌ 月份格式：YYYY-MM；也可输入 YYYY-MM-DD 或 DD/MM/YYYY'); return
    await update.effective_message.reply_text(calc_simple_summary('month',anchor))

async def calc_week_cmd(u,c): await calc_report_cmd(u,c,'week')
async def calc_month_cmd(u,c): await calc_report_cmd(u,c,'month')
async def calc_year_cmd(u,c): await calc_report_cmd(u,c,'year')

async def calc_report_alias_cmd(update, context):
    if not is_calc_chat(update): return
    await calc_report_cmd(update,context,'day')

async def calc_company_cmd(update, context):
    if not is_calc_chat(update): return
    await update.effective_message.reply_text(f'🏢 公司：{calc_company_title()}\nGroup ID：{update.effective_chat.id}\n账本：独立')
    raise ApplicationHandlerStop

async def calc_kpi_cmd(update, context):
    if not is_calc_chat(update): return
    anchor=datetime.now(TZ).date()
    s,e=calc_period_bounds('month',anchor)
    with db() as c:
        rows=c.execute("SELECT kind,amount,currency FROM calc_entries WHERE chat_id=? AND COALESCE(record_date,substr(created_at,1,10)) BETWEEN ? AND ?",(update.effective_chat.id,s.isoformat(),e.isoformat())).fetchall()
    inc=sum((D(r['amount']) for r in rows if r['currency']=='RM' and r['kind']=='income'),D('0'))
    exp=sum((D(r['amount']) for r in rows if r['currency']=='RM' and r['kind']=='expense'),D('0'))
    net=money(inc-exp)
    await update.effective_message.reply_text(f'📈 {calc_company_title()}｜KPI\n📅 {s:%Y-%m}\n\n📥 收入：RM{money(inc):,.2f}\n📤 支出：RM{money(exp):,.2f}\n💰 净额：{("+" if net>=0 else "-")}RM{abs(net):,.2f}\n🏦 当前资金：RM{calc_balance():,.2f}')
    raise ApplicationHandlerStop

def clean_label(s):
    s=re.sub(r'^[\s:：-]+|[\s:：-]+$','',s or '')
    s=re.sub(r'(?i)\b(?:rm|myr|usd|usdt)\b','',s).strip()
    # 卡M1234 and M1234 are same income source
    s=re.sub(r'(?i)^卡\s*(M\d+)$',r'\1',s)
    return s or '未注明'

def classify_expense(label):
    x=label.lower().replace(' ','')
    if any(k in x for k in ['工钱','工資','工资','com','commission']): return '工钱+COM'
    if any(k in x for k in ['屋租','房租']): return '屋租金'
    if '卡租' in x: return '卡租金'
    if any(k in x for k in ['电费','電費','水费','水費','wifi','wi-fi']): return '电+水+WiFi'
    if 'ota' in x: return 'OTA'
    if any(k in x for k in ['广告','廣告','ads']): return '广告'
    return label or '其他支出'

def parse_staff_report(text):
    if not re.search(r'(?im)^\s*(?:Money|Sale)\s*$',text): return None
    mtotal=re.search(r'(?im)^\s*Total\s*[:：]\s*RM\s*([\d,]+(?:\.\d+)?)\s*$',text)
    if not mtotal: return None
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    if len(lines)<2: return None
    dm=re.match(r'^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$',lines[0])
    if not dm: return None
    y=int(dm.group(3)) if dm.group(3) else datetime.now(TZ).year
    if y<100: y+=2000
    try: d=datetime(y,int(dm.group(2)),int(dm.group(1))).date()
    except: return None
    staff=lines[1].strip('()（） ')
    if not staff or len(staff)>50: return None
    sale=money(mtotal.group(1).replace(',','')); expense=money(sale*D('0.15')); net=money(sale-expense); com=money(net*D('0.20'))
    return staff,d,sale,expense,net,com

async def save_staff_report(update,text):
    p=parse_staff_report(text)
    if not p: return False
    staff,d,sale,expense,net,com=p; month=d.strftime('%Y-%m')
    with db() as c:
        c.execute('''INSERT INTO calc_staff_reports_v2(created_at,chat_id,staff,report_date,report_month,sale,expense15,net_sale,com20,raw_text,status)
        VALUES(?,?,?,?,?,?,?,?,?,?, 'pending')
        ON CONFLICT(chat_id,staff,report_month) DO UPDATE SET created_at=excluded.created_at,report_date=excluded.report_date,sale=excluded.sale,expense15=excluded.expense15,net_sale=excluded.net_sale,com20=excluded.com20,raw_text=excluded.raw_text,salary=NULL,payable=NULL,status='pending',posted_entry_id=NULL''',
        (now_iso(),update.effective_chat.id,staff,d.isoformat(),month,str(sale),str(expense),str(net),str(com),text))
    preview=(f'👤 {staff}｜{month} 员工结算\n\n💰 Sale：RM{sale:,.2f}\n📉 开销 15%：RM{expense:,.2f}\n💵 净 Sale：RM{net:,.2f}\n📈 COM 20%：RM{com:,.2f}\n\n💼 工钱：待输入\n💰 当前应付：RM{com:,.2f} + 工钱\n\n⏳ 状态：待确认\nReply 此消息：+工资 3000')
    msg=await update.effective_message.reply_text(preview)
    with db() as c: c.execute('UPDATE calc_staff_reports_v2 SET preview_message_id=? WHERE chat_id=? AND staff=? AND report_month=?',(msg.message_id,update.effective_chat.id,staff,month))
    return True

def pending_by_reply(update):
    r=update.effective_message.reply_to_message
    if not r: return None
    with db() as c: return c.execute('SELECT * FROM calc_staff_reports_v2 WHERE chat_id=? AND preview_message_id=? ORDER BY id DESC LIMIT 1',(update.effective_chat.id,r.message_id)).fetchone()

def _cardfee_month_data(anchor=None):
    if anchor is None: anchor=datetime.now(TZ).date()
    first=anchor.replace(day=1); last=anchor.replace(day=calendar.monthrange(anchor.year,anchor.month)[1])
    bank_names={'M':'MAYBANK','MBB':'MAYBANK','MAYBANK':'MAYBANK','C':'CIMB','CIMB':'CIMB','BR':'BANK RAKYAT','BANKRAKYAT':'BANK RAKYAT','AM':'AM BANK','AMBANK':'AM BANK','AG':'ARGO','ARGO':'ARGO','AF':'AFFIN','AFFIN':'AFFIN','B':'BSN','BSN':'BSN','R':'RHB','RHB':'RHB','BI':'BANK ISLAM','BANKISLAM':'BANK ISLAM','BM':'MUAMALAT','MUAMALAT':'MUAMALAT','H':'HONG LEONG','HLB':'HONG LEONG','HONGLEONG':'HONG LEONG'}
    batches=[]; company={}; banks={}; total_gross=D('0'); total_fee=D('0'); total_net=D('0'); pcs=0
    with db() as c:
        rows=c.execute("SELECT id,record_date,customer_label,codes,card_details,gross,fee,net FROM calc_cardfee_pending WHERE chat_id=? AND status='posted' ORDER BY record_date,id",(calc_scope_chat_id(),)).fetchall()
    for r in rows:
        rd=(r['record_date'] or '')[:10]
        try: d=datetime.strptime(rd,'%Y-%m-%d').date()
        except: continue
        if not (first <= d <= last): continue
        label=(r['customer_label'] or '').strip() or '未命名'
        try: details=json.loads(r['card_details'] or '[]')
        except Exception: details=[]
        clean=[]
        for x in details:
            code=re.sub(r'\s+','',str(x.get('code',''))).upper()
            bank=(x.get('bank') or '').strip()
            if not bank:
                m=re.match(r'[A-Z]+',code); bank=bank_names.get(m.group(0) if m else '','未知银行')
            amt=money(x.get('amount','0'))
            if code:
                clean.append((code,bank,amt)); pcs+=1
                z=banks.setdefault(bank,[0,D('0')]); z[0]+=1; z[1]+=amt
                z=company.setdefault(label,[0,D('0')]); z[0]+=1; z[1]+=amt
        gross=money(r['gross'] or sum((x[2] for x in clean),D('0'))); fee=money(r['fee'] or 0); net=money(r['net'] or (gross-fee))
        total_gross+=gross; total_fee+=fee; total_net+=net
        batches.append((d,label,clean,gross,fee,net))
    return first,batches,company,banks,pcs,money(total_gross),money(total_fee),money(total_net)

def calc_month_codes(anchor=None, full=False):
    first,batches,company,banks,pcs,gross,fee,net=_cardfee_month_data(anchor)
    if not batches: return f'💳 {calc_company_title()}｜{first:%Y-%m} 卡费统计\n\n本月还没有已确认的卡费记录。'
    if not full:
        lines=[f'💳 {calc_company_title()}｜{first:%Y-%m} 卡费｜简易','', '【公司】']
        for name,(n,amt) in sorted(company.items(), key=lambda kv:(-kv[1][1],kv[0])):
            lines.append(f'{name}｜{n} PCS｜RM{money(amt):,.2f}')
        lines.extend(['','【BANK】'])
        for bank,(n,amt) in sorted(banks.items(), key=lambda kv:(-kv[1][1],kv[0])):
            lines.append(f'{bank}｜{n} PCS｜RM{money(amt):,.2f}')
        lines.extend(['','━━━━━━━━━━━━',f'TOTAL：{pcs} PCS',f'Gross：RM{gross:,.2f}',f'Fee：RM{fee:,.2f}',f'Net：RM{net:,.2f}'])
        return '\n'.join(lines)
    lines=[f'📚 {calc_company_title()}｜{first:%Y-%m} 卡费｜完整总账','']
    for d,label,details,bg,bf,bn in batches:
        lines.append(f'{d:%d/%m/%Y}｜{label}')
        for code,bank,amt in details: lines.append(f'{code}｜{bank}｜RM{amt:,.2f}')
        lines.append(f'{len(details)} PCS｜Gross RM{bg:,.2f}｜Fee RM{bf:,.2f}｜Net RM{bn:,.2f}')
        lines.append('')
    lines.append('【公司汇总】')
    for name,(n,amt) in sorted(company.items(), key=lambda kv:(-kv[1][1],kv[0])): lines.append(f'{name}｜{n} PCS｜RM{money(amt):,.2f}')
    lines.extend(['','【BANK汇总】'])
    for bank,(n,amt) in sorted(banks.items(), key=lambda kv:(-kv[1][1],kv[0])): lines.append(f'{bank}｜{n} PCS｜RM{money(amt):,.2f}')
    lines.extend(['','━━━━━━━━━━━━',f'TOTAL：{pcs} PCS',f'Gross：RM{gross:,.2f}',f'Fee：RM{fee:,.2f}',f'Net：RM{net:,.2f}'])
    return '\n'.join(lines)

def calc_eval_expression(text):
    raw=text.strip().replace(',', '').replace('×','*').replace('÷','/')
    if not raw or len(raw)>120 or not re.fullmatch(r'[0-9.()+\-*/\s]+',raw): return None
    try: tree=ast.parse(raw,mode='eval')
    except Exception: return None
    def ev(n):
        if isinstance(n,ast.Expression): return ev(n.body)
        if isinstance(n,ast.Constant) and isinstance(n.value,(int,float)): return D(str(n.value))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            v=ev(n.operand); return v if isinstance(n.op,ast.UAdd) else -v
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=ev(n.left),ev(n.right)
            if isinstance(n.op,ast.Add): return a+b
            if isinstance(n.op,ast.Sub): return a-b
            if isinstance(n.op,ast.Mult): return a*b
            if b==0: raise ZeroDivisionError
            return a/b
        raise ValueError
    try: return money(ev(tree))
    except ZeroDivisionError: return 'DIV0'
    except Exception: return None

def calc_receipt_entry(update):
    r=update.effective_message.reply_to_message if update.effective_message else None
    if not r: return None
    chat_id=update.effective_chat.id
    with db() as c:
        # 1) Reply to the bot's receipt message.
        row=c.execute('SELECT entry_id FROM calc_entry_receipts WHERE chat_id=? AND message_id=?',(chat_id,r.message_id)).fetchone()
        if row: return row
        # 2) Reply to the user's original +/- message (new records).
        row=c.execute('SELECT entry_id FROM calc_entry_receipts WHERE chat_id=? AND source_message_id=?',(chat_id,r.message_id)).fetchone()
        if row: return row
        # 3) Backward-compatible recovery for entries created before source_message_id existed.
        # Match the replied original text to the most recent ledger row from that same sender.
        rtext=(r.text or r.caption or '').strip()
        if rtext:
            uid=getattr(getattr(r,'from_user',None),'id',None)
            if uid is not None:
                ent=c.execute('SELECT id FROM calc_entries WHERE chat_id=? AND user_id=? AND raw_text=? ORDER BY id DESC LIMIT 1',(chat_id,uid,rtext)).fetchone()
            else:
                ent=c.execute('SELECT id FROM calc_entries WHERE chat_id=? AND raw_text=? ORDER BY id DESC LIMIT 1',(chat_id,rtext)).fetchone()
            if ent:
                # Save the recovered link so future replies are exact and fast.
                c.execute('UPDATE calc_entry_receipts SET source_message_id=? WHERE entry_id=?',(r.message_id,int(ent['id'])))
                return {'entry_id': int(ent['id'])}
    return None

def parse_calc_date_text(text):
    """Accept YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, DD/MM/YYYY."""
    t=(text or '').strip()
    for fmt in ('%Y-%m-%d','%Y/%m/%d','%d-%m-%Y','%d/%m/%Y'):
        try:
            return datetime.strptime(t,fmt).date()
        except ValueError:
            pass
    return None

async def calc_date_router(update, context):
    """High-priority date handler for the calculator group.

    Plain date -> view that day's company account.
    Reply + date -> move the replied ledger entry to that accounting date.
    This runs before the generic calculator/backup routers so dates can never be
    mistaken for arithmetic such as 20/8/2026.
    """
    if not is_calc_chat(update) or not update.effective_message:
        return
    text=(update.effective_message.text or '').strip()
    nd=parse_calc_date_text(text)
    if not nd:
        return

    # Reply + date means edit/move this ledger entry; never treat it as a report lookup.
    if update.effective_message.reply_to_message:
        # Card-fee batches are resolved FIRST by the exact Telegram message id.
        # This avoids guessing Gross/Fee from raw_text and works when replying
        # to the original card block or the bot's card preview.
        replied_mid=update.effective_message.reply_to_message.message_id
        with db() as c:
            cf=c.execute("SELECT * FROM calc_cardfee_pending WHERE chat_id=? AND status IN ('posted','pending') AND (source_message_id=? OR preview_message_id=?) ORDER BY id DESC LIMIT 1",
                         (update.effective_chat.id,replied_mid,replied_mid)).fetchone()
            if cf:
                bid=cf['batch_id'] or f"cf:{cf['id']}"
                oldd=cf['record_date'] or str(cf['created_at'])[:10]
                c.execute("UPDATE calc_cardfee_pending SET batch_id=?,record_date=? WHERE id=?",(bid,nd.isoformat(),cf['id']))
                if cf['status']=='pending':
                    try: oldshow=datetime.strptime(oldd,'%Y-%m-%d').strftime('%d/%m/%Y')
                    except Exception: oldshow=oldd
                    await update.effective_message.reply_text(
                        f'✅ 已更新卡费日期（待确认）\n原日期：{oldshow}\n新日期：{nd.strftime("%d/%m/%Y")}')
                    raise ApplicationHandlerStop
                # Bind legacy Gross/Fee rows for THIS exact batch once, then move atomically.
                c.execute("UPDATE calc_entries SET batch_id=? WHERE chat_id=? AND raw_text=? AND (category IN ('卡费','卡费 Fee') OR label IN ('卡费 Gross','卡费 Fee'))",
                          (bid,update.effective_chat.id,cf['raw_text']))
                c.execute("UPDATE calc_entries SET record_date=? WHERE chat_id=? AND batch_id=?",
                          (nd.isoformat(),update.effective_chat.id,bid))
                parts=c.execute("SELECT kind,amount FROM calc_entries WHERE chat_id=? AND batch_id=?",
                                (update.effective_chat.id,bid)).fetchall()
                gross=sum((D(x['amount']) for x in parts if x['kind']=='income'),D('0'))
                fee=sum((D(x['amount']) for x in parts if x['kind']=='expense'),D('0'))
                try: oldshow=datetime.strptime(oldd,'%Y-%m-%d').strftime('%d/%m/%Y')
                except Exception: oldshow=oldd
                await update.effective_message.reply_text(
                    f'✅ 已移动整批卡费日期\n\nGross：RM{gross:,.2f}\nFee：RM{fee:,.2f}\nNet：RM{(gross-fee):,.2f}'
                    f'\n原日期：{oldshow}\n新日期：{nd.strftime("%d/%m/%Y")}'
                    f'\n\n🏦 公司资金：RM{calc_balance():,.2f}（不变）')
                raise ApplicationHandlerStop
        rr=calc_receipt_entry(update)
        if not rr:
            await update.effective_message.reply_text(
                '⚠️ 找不到对应账目。请 Reply 原本的 +/− 记账消息，或 Reply Bot 的入账收据。'
            )
            raise ApplicationHandlerStop
        eid=int(rr['entry_id'])
        row=None
        with db() as c:
            row=c.execute('SELECT * FROM calc_entries WHERE id=? AND chat_id=?',(eid,update.effective_chat.id)).fetchone()
            if row:
                oldd=(row['record_date'] if 'record_date' in row.keys() and row['record_date'] else str(row['created_at'])[:10])
                is_card=(row['category'] in ('卡费','卡费 Fee') or row['label'] in ('卡费 Gross','卡费 Fee'))
                if is_card:
                    batch_id=row['batch_id'] if 'batch_id' in row.keys() else None
                    if not batch_id:
                        pr=c.execute("SELECT id,batch_id,record_date FROM calc_cardfee_pending WHERE chat_id=? AND raw_text=? AND status='posted' ORDER BY id DESC LIMIT 1",
                                     (update.effective_chat.id,row['raw_text'])).fetchone()
                        if pr:
                            batch_id=pr['batch_id'] or f"cf:{pr['id']}"
                            c.execute("UPDATE calc_cardfee_pending SET batch_id=? WHERE id=?",(batch_id,pr['id']))
                            c.execute("UPDATE calc_entries SET batch_id=? WHERE chat_id=? AND raw_text=? AND (category IN ('卡费','卡费 Fee') OR label IN ('卡费 Gross','卡费 Fee'))",
                                      (batch_id,update.effective_chat.id,row['raw_text']))
                    if batch_id:
                        pr=c.execute("SELECT record_date FROM calc_cardfee_pending WHERE chat_id=? AND batch_id=? ORDER BY id DESC LIMIT 1",
                                     (update.effective_chat.id,batch_id)).fetchone()
                        if pr and pr['record_date']:
                            oldd=pr['record_date']
                        c.execute("UPDATE calc_entries SET record_date=? WHERE chat_id=? AND batch_id=?",
                                  (nd.isoformat(),update.effective_chat.id,batch_id))
                        c.execute("UPDATE calc_cardfee_pending SET record_date=? WHERE chat_id=? AND batch_id=?",
                                  (nd.isoformat(),update.effective_chat.id,batch_id))
                    else:
                        c.execute("UPDATE calc_entries SET record_date=? WHERE chat_id=? AND raw_text=? AND (category IN ('卡费','卡费 Fee') OR label IN ('卡费 Gross','卡费 Fee'))",
                                  (nd.isoformat(),update.effective_chat.id,row['raw_text']))
                        c.execute("UPDATE calc_cardfee_pending SET record_date=? WHERE chat_id=? AND raw_text=? AND status='posted'",
                                  (nd.isoformat(),update.effective_chat.id,row['raw_text']))
                else:
                    c.execute('UPDATE calc_entries SET record_date=? WHERE id=?',(nd.isoformat(),eid))
        if not row:
            await update.effective_message.reply_text('⚠️ 找不到这笔记录。')
            raise ApplicationHandlerStop
        try:
            oldshow=datetime.strptime(oldd,'%Y-%m-%d').strftime('%d/%m/%Y')
        except Exception:
            oldshow=oldd
        label=row['label'] or row['category'] or '其他'
        is_card_batch=(row['category'] in ('卡费','卡费 Fee') or row['label'] in ('卡费 Gross','卡费 Fee'))
        sign='+' if row['kind']=='income' else '-'
        if is_card_batch:
            with db() as c:
                bid=row['batch_id'] if 'batch_id' in row.keys() else None
                if bid:
                    parts=c.execute("SELECT kind,amount FROM calc_entries WHERE chat_id=? AND batch_id=?",
                                    (update.effective_chat.id,bid)).fetchall()
                else:
                    parts=c.execute("SELECT kind,amount FROM calc_entries WHERE chat_id=? AND raw_text=? AND (category IN ('卡费','卡费 Fee') OR label IN ('卡费 Gross','卡费 Fee'))",
                                    (update.effective_chat.id,row['raw_text'])).fetchall()
            gross=sum((D(x['amount']) for x in parts if x['kind']=='income'),D('0'))
            fee=sum((D(x['amount']) for x in parts if x['kind']=='expense'),D('0'))
            await update.effective_message.reply_text(
                f'✅ 已移动整批卡费日期\n\nGross：RM{gross:,.2f}\nFee：RM{fee:,.2f}\nNet：RM{(gross-fee):,.2f}'
                f'\n原日期：{oldshow}\n新日期：{nd.strftime("%d/%m/%Y")}'
                f'\n\n🏦 公司资金：RM{calc_balance():,.2f}（不变）'
            )
        else:
            await update.effective_message.reply_text(
                f'✅ 已移动账目日期\n\n项目：{label}\n金额：{sign}RM{D(row["amount"]):,.2f}'
                f'\n原日期：{oldshow}\n新日期：{nd.strftime("%d/%m/%Y")}'
                f'\n\n🏦 公司资金：RM{calc_balance():,.2f}（不变）'
            )
        raise ApplicationHandlerStop

    # A standalone date is a shortcut for /report DATE.
    await update.effective_message.reply_text(calc_summary('day',nd))
    raise ApplicationHandlerStop

async def calc_entry_callback(update, context):
    q=update.callback_query
    if not q or not q.data or not q.message or q.message.chat.id not in calc_chat_ids(): return
    _calc_scope_chat.set(int(q.message.chat.id))
    m=re.fullmatch(r'ceundo:(\d+)',q.data)
    if not m: return
    await q.answer(); eid=int(m.group(1))
    with db() as c:
        row=c.execute('SELECT * FROM calc_entries WHERE id=? AND chat_id=?',(eid,q.message.chat.id)).fetchone()
        if not row:
            await q.answer('这笔已经撤销或不存在',show_alert=True); return
        bid=(row['batch_id'] or '') if 'batch_id' in row.keys() else ''
        if bid.startswith('qfee:'):
            ids=[int(x['id']) for x in c.execute('SELECT id FROM calc_entries WHERE chat_id=? AND batch_id=?',(q.message.chat.id,bid)).fetchall()]
            c.execute('DELETE FROM calc_entries WHERE chat_id=? AND batch_id=?',(q.message.chat.id,bid))
            for rid in ids:
                c.execute('DELETE FROM calc_entry_receipts WHERE entry_id=?',(rid,))
        else:
            c.execute('DELETE FROM calc_entries WHERE id=?',(eid,)); c.execute('DELETE FROM calc_entry_receipts WHERE entry_id=?',(eid,))
    await q.edit_message_text(q.message.text+f'\n\n↩️ 已撤销此笔\n🏦 公司资金：RM{calc_balance():,.2f}')

async def cardfee_callback(update, context):
    q=update.callback_query
    if not q or not q.data or not q.message or q.message.chat.id not in calc_chat_ids(): return
    _calc_scope_chat.set(int(q.message.chat.id))
    m=re.fullmatch(r'cf(ok|cancel):(\d+)',q.data)
    if not m: return
    await q.answer()
    pid=int(m.group(2))
    with db() as c: cf=c.execute('SELECT * FROM calc_cardfee_pending WHERE id=? AND chat_id=?',(pid,q.message.chat.id)).fetchone()
    if not cf:
        await q.answer('找不到这笔记录',show_alert=True); return
    if cf['status']!='pending':
        await q.answer('这笔已经处理过',show_alert=True); return
    if m.group(1)=='cancel':
        with db() as c: c.execute("UPDATE calc_cardfee_pending SET status='cancelled',posted_at=? WHERE id=?",(now_iso(),pid))
        await q.edit_message_text(q.message.text+'\n\n❌ 已取消｜没有入公司账')
        return
    with db() as c:
        bid=cf['batch_id'] if 'batch_id' in cf.keys() and cf['batch_id'] else f'cf:{pid}'
        c.execute("UPDATE calc_cardfee_pending SET batch_id=? WHERE id=?",(bid,pid))
        c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,batch_id) VALUES(?,?,?,?,?,'RM','卡费','卡费 Gross',?,1,?,?)",
                  (now_iso(),q.message.chat.id,q.from_user.id,'income',cf['gross'],cf['raw_text'],cf['record_date'],bid))
        if D(cf['fee'])>0:
            c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,batch_id) VALUES(?,?,?,?,?,'RM','卡费 Fee','卡费 Fee',?,1,?,?)",
                      (now_iso(),q.message.chat.id,q.from_user.id,'expense',cf['fee'],cf['raw_text'],cf['record_date'],bid))
        c.execute("UPDATE calc_cardfee_pending SET status='posted',posted_at=? WHERE id=?",(now_iso(),pid))
    await q.edit_message_text(q.message.text+f'\n\n✅ 已确认入账\n🏦 公司资金：RM{calc_balance():,.2f}')


# Flexible bank-code/card-fee parser used by every calculator company group.
# A card code always ends with exactly 4 digits. Money may appear before or
# after the code, with or without RM. Example: B1867 1200 / RM1200 BSN1867.
_CARD_BANK_NAMES={
    'M':'MAYBANK','MBB':'MAYBANK','MAYBANK':'MAYBANK',
    'C':'CIMB','CIMB':'CIMB',
    'BR':'BANK RAKYAT','BANKRAKYAT':'BANK RAKYAT',
    'AM':'AM BANK','AMBANK':'AM BANK',
    'AG':'ARGO','ARGO':'ARGO',
    'AF':'AFFIN','AFFIN':'AFFIN',
    'B':'BSN','BSN':'BSN',
    'R':'RHB','RHB':'RHB',
    'BI':'BANK ISLAM','BANKISLAM':'BANK ISLAM',
    'BM':'MUAMALAT','MUAMALAT':'MUAMALAT',
    'H':'HONG LEONG','HLB':'HONG LEONG','HONGLEONG':'HONG LEONG',
}
_CARD_BANK_ALIAS_RE=(
    r'(?:BANK\s*RAKYAT|BANK\s*ISLAM|HONG\s*LEONG|AM\s*BANK|'
    r'MAYBANK|MUAMALAT|CIMB|AMBANK|MBB|RHB|BSN|BR|AM|AG|ARGO|AF|AFFIN|BI|BM|HLB|M|C|B|R|H)'
)

def _norm_card_bank_alias(v):
    return re.sub(r'\s+','',(v or '')).upper()

def _parse_card_code_money_line(line):
    """Return one card detail from a line, or None.

    Accepted examples:
      BSN1867 1200
      B1867 RM1200
      RM1200 BSN1867 备注
      1200 B1867 备注
      BSN1867 备注              # amount=None; assigned from 卡费 RM... Xn
    """
    line=(line or '').strip()
    if not line:
        return None
    num=r'([\d,]+(?:\.\d+)?)'
    # Amount BEFORE bank/code.
    m=re.match(
        rf'^\s*(?:RM\s*)?{num}\s+({_CARD_BANK_ALIAS_RE})\s*[-:]?\s*(\d{{4}})\b(?:\s+(.*))?$',
        line,re.I)
    if m:
        amt=money(m.group(1).replace(',',''))
        prefix=_norm_card_bank_alias(m.group(2))
        digits=m.group(3)
        note=(m.group(4) or '').strip()
        return {'code':prefix+digits,'prefix':prefix,'bank':_CARD_BANK_NAMES.get(prefix,'未知银行'),'amount':amt,'note':note}

    # Bank/code BEFORE optional amount. Anything after the optional amount is a note.
    m=re.match(
        rf'^\s*({_CARD_BANK_ALIAS_RE})\s*[-:]?\s*(\d{{4}})\b(?:\s+(?:RM\s*)?{num})?(?:\s+(.*))?$',
        line,re.I)
    if not m:
        return None
    prefix=_norm_card_bank_alias(m.group(1))
    digits=m.group(2)
    amt=money(m.group(3).replace(',','')) if m.group(3) else None
    note=(m.group(4) or '').strip()
    return {'code':prefix+digits,'prefix':prefix,'bank':_CARD_BANK_NAMES.get(prefix,'未知银行'),'amount':amt,'note':note}



def _calc_annotated_money_expression(line):
    """Evaluate RM-labelled arithmetic hidden inside a note line.

    Examples:
      post RM7.80 + llm RM8      -> 15.80
      courier RM10 * 2           -> 20.00
      charge RM30 / 3 + RM2      -> 12.00

    Words between money tokens are ignored, but at least one arithmetic
    operator must be present. This is intentionally only used for card-fee
    note lines, never for the card header / code rows / declared Total.
    """
    src=(line or '').strip()
    if not src:
        return None
    # Need an RM-labelled amount so ordinary notes/dates/codes cannot become math.
    if not re.search(r'(?i)\bRM\s*[0-9]', src):
        return None
    # Keep RM amounts, plain numeric operands that directly follow an operator,
    # parentheses and arithmetic operators. Labels such as "post"/"llm" are ignored.
    parts=[]
    token_re=re.compile(r'(?i)RM\s*([\d,]+(?:\.\d+)?)|([+\-*/×÷])|([()])|(?<![A-Za-z0-9])([\d,]+(?:\.\d+)?)(?![A-Za-z0-9])')
    for m in token_re.finditer(src):
        if m.group(1) is not None:
            parts.append(m.group(1).replace(',',''))
        elif m.group(2) is not None:
            parts.append(m.group(2).replace('×','*').replace('÷','/'))
        elif m.group(3) is not None:
            parts.append(m.group(3))
        elif m.group(4) is not None:
            # Only accept an unlabelled number when it is an arithmetic operand,
            # e.g. "RM10 * 2". Do not pull dates/codes into the expression.
            prev=''.join(parts[-1:])
            if prev in {'+','-','*','/','('}:
                parts.append(m.group(4).replace(',',''))
    expr=' '.join(parts)
    if not any(op in expr for op in '+-*/'):
        return None
    result=calc_eval_expression(expr)
    if result in (None,'DIV0'):
        return result
    return money(result)

def _card_note_math_fee(text):
    """Return (total, breakdown) for arithmetic note charges in a card-fee block."""
    total=D('0'); breakdown=[]
    for raw in (text or '').splitlines():
        line=raw.strip()
        if not line:
            continue
        # Structural card-fee lines are never treated as extra charges.
        if re.search(r'(?i)卡费\s*(?:RM\s*)?[\d,]+(?:\.\d+)?\s*[xX×]\s*\d+', line):
            continue
        if re.match(r'(?i)^\s*T(?:otal)?\s*[:：]', line):
            continue
        if _parse_card_code_money_line(line):
            continue
        if re.search(r'(?i)-\s*[\d,]+(?:\.\d+)?\s*fee\b', line):
            continue
        val=_calc_annotated_money_expression(line)
        if val=='DIV0':
            return 'DIV0', breakdown
        if val is not None:
            total += D(val)
            breakdown.append((line, money(val)))
    return money(total), breakdown

def _card_record_date_and_label(text):
    """Read an optional date from the first non-empty line; default to MY today."""
    today=datetime.now(TZ).date()
    first=next((x.strip() for x in (text or '').splitlines() if x.strip()),'')
    pats=(
        (r'^(\d{1,2})/(\d{1,2})/(\d{4})\s*(.*)$','dmy'),
        (r'^(\d{1,2})-(\d{1,2})-(\d{4})\s*(.*)$','dmy'),
        (r'^(\d{4})-(\d{1,2})-(\d{1,2})\s*(.*)$','ymd'),
        (r'^(\d{4})/(\d{1,2})/(\d{1,2})\s*(.*)$','ymd'),
    )
    for pat,kind in pats:
        m=re.match(pat,first)
        if not m: continue
        try:
            if kind=='dmy': d=datetime(int(m.group(3)),int(m.group(2)),int(m.group(1))).date()
            else: d=datetime(int(m.group(1)),int(m.group(2)),int(m.group(3))).date()
            return d.isoformat(), (m.group(4) or '').strip(' -—_')
        except ValueError:
            return today.isoformat(), ''
    return today.isoformat(), ''

async def calc_text_router(update, context):
    if not is_calc_chat(update): return
    # In the main company group, a photo is only a voucher/evidence image.
    # If it has a caption, the caption is processed exactly like a typed ledger command.
    text=(update.effective_message.text or update.effective_message.caption or '').strip()
    if not text: raise ApplicationHandlerStop

    # Safe same-day chatter cleanup, e.g. 清除 5:00pm 前的废话.
    cutoff_parts=_parse_cleanup_cutoff(text)
    if cutoff_parts is not None:
        await _delete_company_chatter_before(update, context, cutoff_parts[0], cutoff_parts[1])
        raise ApplicationHandlerStop

    # Calculator view aliases: Simplified / Traditional / English; English is case-insensitive.
    # Telegram does not support Chinese slash commands reliably, so Chinese aliases are handled as plain text too.
    first, *rest = text.split(maxsplit=1)
    token=first.lstrip('/').strip()
    low=token.lower()
    arg=rest[0].strip() if rest else ''

    # Reply an existing message with just L or W to classify the whole replied
    # expense record/block without retyping it. Repeating the Reply changes the
    # classification in-place and never duplicates/deducts the company balance.
    if re.fullmatch(r'\s*[LWlw]\s*', text) and update.effective_message.reply_to_message:
        tag=text.strip().upper()
        n,changed,kind=_record_or_reclassify_lw_reply(update,update.effective_message.reply_to_message,tag)
        if not n:
            await update.effective_message.reply_text('⚠️ 这条 Reply 里找不到可归类的费用金额。')
        else:
            mtotal=_funder_month_total(tag)
            if kind=='formal':
                await update.effective_message.reply_text(
                    f'✅ 已归类为 {tag} 出资\n这笔原本已是正式公司账，不重复扣余额。\n本月 {tag} 累计：RM{mtotal:,.2f}')
            else:
                action='已改为' if changed else '已归类为'
                await update.effective_message.reply_text(
                    f'✅ {action} {tag} 垫付\n共 {n} 笔｜未扣公司余额\n本月 {tag} 累计：RM{mtotal:,.2f}')
        raise ApplicationHandlerStop

    # L/W advance attribution. Without an explicit +/- this records who advanced the money
    # but DOES NOT change the company balance. Supports new and old formats, including multi-line history.
    if re.fullmatch(r'\s*([LWlw])\s*(?:费用|費用)\s*', text, re.I):
        tag=re.fullmatch(r'\s*([LWlw])\s*(?:费用|費用)\s*', text, re.I).group(1).upper()
        await _reply_long_text(update.effective_message, calc_funder_report(tag))
        raise ApplicationHandlerStop

    sm=re.fullmatch(r'\s*(?:结算|結算)\s*([LWlw])\s*(?:费用|費用)?\s*|\s*([LWlw])\s*(?:费用|費用)?\s*(?:结算|結算)\s*', text, re.I)
    if sm:
        if not (int(update.effective_user.id)==int(OWNER_ID) or bot_admin_has(int(update.effective_user.id))):
            await update.effective_message.reply_text('⛔ 只有 Owner / Bot Admin 可以结算 L/W 垫付')
            raise ApplicationHandlerStop
        tag=(sm.group(1) or sm.group(2)).upper()
        eid,n,total=_settle_lw_advances(tag)
        if not eid:
            await update.effective_message.reply_text(f'ℹ️ {tag} 本月没有待结算垫付。')
        else:
            await update.effective_message.reply_text(f'✅ {tag}垫付已结算\n共 {n} 笔｜RM{total:,.2f}\n已正式计入公司支出一次。')
        raise ApplicationHandlerStop

    advance_rows=_parse_lw_advance_block(text)
    if advance_rows:
        _record_lw_advances(update,advance_rows)
        totals={'L':D('0'),'W':D('0')}
        for tag,amt,label,recdate,raw in advance_rows: totals[tag]+=D(amt)
        parts=[f'{k}：RM{money(v):,.2f}' for k,v in totals.items() if v]
        month_parts=[]
        for k in ('L','W'):
            if totals[k]: month_parts.append(f'本月{k}累计：RM{_funder_month_total(k):,.2f}')
        await update.effective_message.reply_text(
            '💼 垫付已记录（未扣公司余额）\n'
            + f'共 {len(advance_rows)} 笔｜' + '｜'.join(parts)
            + ('\n'+'｜'.join(month_parts) if month_parts else '')
            + f'\n🏦 公司余额保持：RM{calc_balance():,.2f}\n月尾可用：结算L费用 / 结算W费用')
        raise ApplicationHandlerStop

    # Universal company-ledger search. In calculator groups, 查/check/find are
    # ledger queries (date / code / amount / exact user note), so they cannot
    # collide with the Lead /find handler registered later.
    if token=='查' or low in {'check','find'}:
        if not arg:
            await update.effective_message.reply_text('用法：查 BSN1867 / check 广告费 / find 1200 / 查 11/09/2026 / 查 BSN1867 11/09/2026')
            raise ApplicationHandlerStop
        await _reply_long_text(update.effective_message, calc_ledger_search(arg))
        raise ApplicationHandlerStop

    card_simple_alias={'代号','代號','卡代号','卡代號','卡费简易','卡費簡易'}
    card_full_alias={'卡费完整','卡費完整','卡费总账','卡費總賬','卡费大账','卡費大賬'}
    if token in card_simple_alias or low in {'code','codes','codes_simple','card_simple'}:
        ym=arg or datetime.now(TZ).strftime('%Y-%m')
        try: anchor=datetime.strptime(ym+'-01','%Y-%m-%d').date()
        except ValueError:
            await update.effective_message.reply_text('❌ 月份格式：YYYY-MM'); raise ApplicationHandlerStop
        await update.effective_message.reply_text(calc_month_codes(anchor,False)); raise ApplicationHandlerStop
    if token in card_full_alias or low in {'codes_full','card_full','card_ledger'}:
        ym=arg or datetime.now(TZ).strftime('%Y-%m')
        try: anchor=datetime.strptime(ym+'-01','%Y-%m-%d').date()
        except ValueError:
            await update.effective_message.reply_text('❌ 月份格式：YYYY-MM'); raise ApplicationHandlerStop
        await update.effective_message.reply_text(calc_month_codes(anchor,True)); raise ApplicationHandlerStop
    simple_alias={'简易账','簡易賬','简易帐','簡易帳'}
    full_alias={'总账','總賬','总帐','總帳','大账','大賬','大帐','大帳','账单','賬單'}
    if token in simple_alias or low in {'simple','summary'}:
        anchor=None
        if not arg:
            anchor=datetime.now(TZ).date()
        else:
            try:
                anchor=datetime.strptime(arg,'%Y-%m').date()
            except Exception:
                anchor=parse_anchor(arg,'day')
        if not anchor:
            await update.effective_message.reply_text('❌ 月份格式：YYYY-MM；也可输入日期'); raise ApplicationHandlerStop
        await update.effective_message.reply_text(calc_simple_summary('month',anchor)); raise ApplicationHandlerStop
    if token in full_alias or low in {'full','ledger','bill'}:
        anchor=parse_anchor(arg,'day') if arg else datetime.now(TZ).date()
        if not anchor:
            await update.effective_message.reply_text('❌ 日期格式：YYYY-MM-DD'); raise ApplicationHandlerStop
        await _reply_long_text(update.effective_message, calc_full_ledger('day',anchor)); raise ApplicationHandlerStop

    # Undo a normal +/- ledger entry by replying to its bot receipt or original +/- message.
    if text.casefold() in {'取消','取消记录','取消記錄','cancel','undo'}:
        rr=calc_receipt_entry(update)
        if rr:
            eid=int(rr['entry_id'])
            with db() as c:
                row=c.execute('SELECT * FROM calc_entries WHERE id=? AND chat_id=?',(eid,update.effective_chat.id)).fetchone()
                if row:
                    bid=(row['batch_id'] or '') if 'batch_id' in row.keys() else ''
                    if bid.startswith('qfee:'):
                        ids=[int(x['id']) for x in c.execute('SELECT id FROM calc_entries WHERE chat_id=? AND batch_id=?',(update.effective_chat.id,bid)).fetchall()]
                        c.execute('DELETE FROM calc_entries WHERE chat_id=? AND batch_id=?',(update.effective_chat.id,bid))
                        for rid in ids:
                            c.execute('DELETE FROM calc_entry_receipts WHERE entry_id=?',(rid,))
                    else:
                        c.execute('DELETE FROM calc_entries WHERE id=?',(eid,)); c.execute('DELETE FROM calc_entry_receipts WHERE entry_id=?',(eid,))
            await update.effective_message.reply_text(f'↩️ 已撤销此笔\n🏦 公司资金：RM{calc_balance():,.2f}' if row else '⚠️ 这笔已经撤销。')
            raise ApplicationHandlerStop

    # Common calculator shorthand: "7320 to u 广告" means an unsettled
    # advertising expense in U. Every calculator chat keeps its own U bucket.
    to_u = re.fullmatch(
        r"(?i)\s*([\d,]+(?:\.\d+)?)\s*to\s*(?:u|usd|usdt)\s*(?:广告|廣告|ads?)\s*",
        text,
    )
    if to_u:
        amount = money(to_u.group(1).replace(',', ''))
        with db() as c:
            c.execute(
                """INSERT INTO calc_entries(
                   created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled
                   ) VALUES(?,?,?,?,?,'U','广告','广告',?,0)""",
                (now_iso(), update.effective_chat.id, update.effective_user.id,
                 'expense', str(amount), text),
            )
        await update.effective_message.reply_text(
            f"✅ 已记录广告：{amount:,.2f} U\n"
            "📢 等输入结算汇率后才换算并扣除 RM 公司资金。"
        )
        raise ApplicationHandlerStop

    # Smart dated expense shorthand. A date makes the intent explicit, so a leading '-' is optional.
    # Supported examples:
    #   topup 30 17/09/26
    #   topup 30 17-09-2026
    #   17/09/2026 topup 30
    # L/W variants are handled above by the advance parser and do not reach here.
    dated_work=text
    dated_m=re.search(r'(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)', dated_work)
    if dated_m and not text.startswith(('+','-')):
        day,mon=int(dated_m.group(1)),int(dated_m.group(2)); yr=int(dated_m.group(3))
        if yr < 100: yr += 2000
        try:
            rec_date=datetime(yr,mon,day).date()
        except ValueError:
            rec_date=None
        if rec_date:
            without_date=(dated_work[:dated_m.start()]+' '+dated_work[dated_m.end():]).strip()
            # label amount OR amount label; require some non-numeric label text to avoid treating a bare date+number as an expense.
            dm_exp=re.fullmatch(r'(.+?)\s+(?:RM|MYR)?\s*([\d,]+(?:\.\d+)?)\s*', without_date, re.I)
            if not dm_exp:
                dm_exp=re.fullmatch(r'(?:RM|MYR)?\s*([\d,]+(?:\.\d+)?)\s+(.+?)\s*', without_date, re.I)
                if dm_exp:
                    raw_amt,raw_label=dm_exp.group(1),dm_exp.group(2)
                else:
                    raw_amt=raw_label=None
            else:
                raw_label,raw_amt=dm_exp.group(1),dm_exp.group(2)
            if raw_amt is not None and raw_label is not None and re.search(r'[A-Za-z\u4e00-\u9fff]', raw_label):
                amount=money(raw_amt.replace(',',''))
                label=clean_label(raw_label)
                category=classify_expense(label)
                with db() as c:
                    cur=c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date) VALUES(?,?,?,?,?,'RM',?,?,?,1,?)",
                                  (now_iso(),update.effective_chat.id,update.effective_user.id,'expense',str(amount),category,label,text,rec_date.isoformat()))
                    eid=cur.lastrowid
                kb=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ 撤销此笔',callback_data=f'ceundo:{eid}')]])
                msg=await update.effective_message.reply_text(
                    f'✅ 已记录支出：RM{amount:,.2f}｜{label}\n📅 日期：{rec_date.strftime("%d/%m/%Y")}\n🏦 公司资金：RM{calc_balance():,.2f}',
                    reply_markup=kb)
                with db() as c:
                    c.execute('INSERT OR REPLACE INTO calc_entry_receipts(entry_id,chat_id,message_id,source_message_id,created_at) VALUES(?,?,?,?,?)',
                              (eid,update.effective_chat.id,msg.message_id,update.effective_message.message_id,now_iso()))
                raise ApplicationHandlerStop

    # Pure arithmetic is calculator-only. A leading + or - is the explicit ledger signal.
    if not text.startswith(('+','-')):
        result=calc_eval_expression(text)
        if result=='DIV0':
            await update.effective_message.reply_text('❌ 不能除以 0'); raise ApplicationHandlerStop
        if result is not None and any(op in text for op in '+-*/×÷'):
            await update.effective_message.reply_text(f'🧮 {text} = {result:,.2f}\n（只计算，不入公司账）'); raise ApplicationHandlerStop

    # Card-fee income -> preview first; only posts after Confirm / 1.
    # Supported input styles include the legacy block AND direct code+money lines.
    # Code must end in 4 digits. Amount may be before/after code and RM is optional.
    card_rows = re.findall(r'卡费\s*(?:RM\s*)?([\d,]+(?:\.\d+)?)\s*[xX×]\s*(\d+)', text, re.I)
    parsed_codes=[]
    for line in text.splitlines():
        d=_parse_card_code_money_line(line)
        if d:
            parsed_codes.append(d)

    # Direct shorthand is intentionally recognized only when every parsed code
    # has an explicit amount, e.g. "B1867 1200" or "RM1200 B1867". This avoids
    # treating a bare bank code/note as a money entry.
    direct_card = bool(parsed_codes) and all(x['amount'] is not None for x in parsed_codes)
    if card_rows or direct_card:
        if card_rows:
            gross = money(sum((D(a.replace(',','')) * D(q) for a,q in card_rows), D('0')))
            expected_amounts=[]
            for a,q in card_rows:
                expected_amounts.extend([money(a.replace(',',''))] * int(q))
            expected_qty=len(expected_amounts)
            if len(parsed_codes)!=expected_qty:
                await update.effective_message.reply_text(
                    f'❌ 卡数量对不上，暂不入账。\n卡费数量：{expected_qty}\nCode 明细：{len(parsed_codes)}')
                raise ApplicationHandlerStop
            details=[]
            for idx,d in enumerate(parsed_codes):
                amt=d['amount'] if d['amount'] is not None else expected_amounts[idx]
                details.append({'code':d['code'],'bank':d['bank'],'amount':str(amt),'note':d.get('note','')})
            detail_total=money(sum((D(x['amount']) for x in details),D('0')))
            if detail_total != gross:
                await update.effective_message.reply_text(
                    f'❌ Code 金额对不上，暂不入账。\n卡费 Gross：RM{gross:,.2f}\nCode 合计：RM{detail_total:,.2f}')
                raise ApplicationHandlerStop
        else:
            # No "卡费 RM... Xn" header: explicit code amounts define the batch.
            details=[{'code':d['code'],'bank':d['bank'],'amount':str(d['amount']),'note':d.get('note','')} for d in parsed_codes]
            gross=money(sum((D(x['amount']) for x in details),D('0')))
            expected_qty=len(details)

        record_date,customer_label=_card_record_date_and_label(text)

        # Optional T validation. Example: T: 1,200 or T: 1,200-1Fee
        tm = re.search(r'(?im)^\s*T(?:otal)?\s*[:：]\s*(?:RM\s*)?([\d,]+(?:\.\d+)?)', text)
        declared = money(tm.group(1).replace(',','')) if tm else None
        if declared is not None and declared != gross:
            await update.effective_message.reply_text(
                f'❌ 卡费 Total 对不上，暂不入账。\n计算：RM{gross:,.2f}\n你写的 T：RM{declared:,.2f}')
            raise ApplicationHandlerStop

        fm = re.search(r'(?i)-\s*([\d,]+(?:\.\d+)?)\s*fee\b', text)
        explicit_fee = money(fm.group(1).replace(',','')) if fm else money('0')
        note_fee, note_fee_breakdown = _card_note_math_fee(text)
        if note_fee == 'DIV0':
            await update.effective_message.reply_text('❌ 费用算式不能除以 0。')
            raise ApplicationHandlerStop
        fee = money(explicit_fee + D(note_fee))
        net=money(gross-fee)
        if fee < 0 or fee > gross:
            await update.effective_message.reply_text('❌ Fee 金额不正确。')
            raise ApplicationHandlerStop

        codes=[x['code'] for x in details]
        detail='\n'.join(
            f"{x['code']} → {x['bank']} → RM{D(x['amount']):,.2f}" + (f"｜{x['note']}" if x.get('note') else '')
            for x in details)
        with db() as c:
            cur=c.execute(
                "INSERT INTO calc_cardfee_pending(created_at,chat_id,user_id,gross,fee,net,codes,raw_text,status,record_date,customer_label,card_details) VALUES(?,?,?,?,?,?,?,?, 'pending',?,?,?)",
                (now_iso(),update.effective_chat.id,update.effective_user.id,str(gross),str(fee),str(net),json.dumps(codes,ensure_ascii=False),text,record_date,customer_label,json.dumps(details,ensure_ascii=False)))
            pid=cur.lastrowid
            c.execute("UPDATE calc_cardfee_pending SET batch_id=?, source_message_id=? WHERE id=?",
                      (f'cf:{pid}', update.effective_message.message_id, pid))
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ 确认入账',callback_data=f'cfok:{pid}'),InlineKeyboardButton('❌ 取消',callback_data=f'cfcancel:{pid}')]])
        title=f"💳 卡处理｜{record_date}" + (f"｜{customer_label}" if customer_label else '')
        fee_calc=''
        if note_fee_breakdown:
            fee_calc='\n费用自动计算：' + ' + '.join(f'RM{v:,.2f}' for _,v in note_fee_breakdown) + f' = RM{D(note_fee):,.2f}'
        msg=await update.effective_message.reply_text(
            f'{title}\n⏳ 待确认\n\n{detail}\n\n处理卡数：{len(details)}\nGross：RM{gross:,.2f}\nFee：-RM{fee:,.2f}\nNet：RM{net:,.2f}{fee_calc}\n\n1 = 确认入账\n2 = 取消',
            reply_markup=kb)
        with db() as c:
            c.execute('UPDATE calc_cardfee_pending SET preview_message_id=? WHERE id=?',(msg.message_id,pid))
        raise ApplicationHandlerStop

    # Numeric fallback for card-fee confirmation: Reply preview with 1 or 2.
    if text in ('1','2') and update.effective_message.reply_to_message:
        mid=update.effective_message.reply_to_message.message_id
        with db() as c: cf=c.execute("SELECT * FROM calc_cardfee_pending WHERE preview_message_id=? AND chat_id=? ORDER BY id DESC LIMIT 1",(mid,update.effective_chat.id)).fetchone()
        if cf:
            if cf['status']!='pending':
                await update.effective_message.reply_text('⚠️ 这笔卡费已经处理过。'); raise ApplicationHandlerStop
            if text=='2':
                with db() as c: c.execute("UPDATE calc_cardfee_pending SET status='cancelled',posted_at=? WHERE id=?",(now_iso(),cf['id']))
                await update.effective_message.reply_text('❌ 已取消，这笔卡费没有入公司账。'); raise ApplicationHandlerStop
            with db() as c:
                bid=cf['batch_id'] if 'batch_id' in cf.keys() and cf['batch_id'] else f"cf:{cf['id']}"
                c.execute("UPDATE calc_cardfee_pending SET batch_id=? WHERE id=?",(bid,cf['id']))
                c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,batch_id) VALUES(?,?,?,?,?,'RM','卡费','卡费 Gross',?,1,?,?)",
                          (now_iso(),update.effective_chat.id,update.effective_user.id,'income',cf['gross'],cf['raw_text'],cf['record_date'],bid))
                if D(cf['fee'])>0:
                    c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,batch_id) VALUES(?,?,?,?,?,'RM','卡费 Fee','卡费 Fee',?,1,?,?)",
                              (now_iso(),update.effective_chat.id,update.effective_user.id,'expense',cf['fee'],cf['raw_text'],cf['record_date'],bid))
                c.execute("UPDATE calc_cardfee_pending SET status='posted',posted_at=? WHERE id=?",(now_iso(),cf['id']))
            await update.effective_message.reply_text(f"✅ 卡费已入公司账\nNet：RM{D(cf['net']):,.2f}\n🏦 公司资金：RM{calc_balance():,.2f}")
            raise ApplicationHandlerStop

    # staff salary / post expense actions must be replies to settlement preview
    pr=pending_by_reply(update)
    if pr:
        sm=re.fullmatch(r'\+\s*工资\s*([\d,]+(?:\.\d+)?)',text,re.I)
        if sm:
            salary=money(sm.group(1).replace(',','')); payable=money(D(pr['com20'])+salary)
            with db() as c: c.execute('UPDATE calc_staff_reports_v2 SET salary=?,payable=? WHERE id=?',(str(salary),str(payable),pr['id']))
            msg=await update.effective_message.reply_text(f"👤 {pr['staff']}｜{pr['report_month']} 员工结算\n\nSale：RM{D(pr['sale']):,.2f}\n开销 15%：RM{D(pr['expense15']):,.2f}\n净 Sale：RM{D(pr['net_sale']):,.2f}\nCOM 20%：RM{D(pr['com20']):,.2f}\n工钱：RM{salary:,.2f}\n\n💰 应付：RM{payable:,.2f}\n⏳ 待确认\n\n确认后 Reply：/公司支出")
            with db() as c: c.execute('UPDATE calc_staff_reports_v2 SET preview_message_id=? WHERE id=?',(msg.message_id,pr['id']))
            raise ApplicationHandlerStop
        if text in ['/公司支出','公司支出']:
            if pr['status']=='posted': await update.effective_message.reply_text('⚠️ 这份员工结算已经入公司账，不能重复扣。'); raise ApplicationHandlerStop
            if pr['salary'] is None: await update.effective_message.reply_text('❌ 还没有输入工钱。先 Reply：+工资 3000'); raise ApplicationHandlerStop
            payable=money(pr['payable'])
            with db() as c:
                cur=c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled) VALUES(?,?,?,?,?,'RM',?,?,?,1)",(now_iso(),update.effective_chat.id,update.effective_user.id,'expense',str(payable),'工钱+COM',f"工钱+COM（{pr['staff']}）",text))
                eid=cur.lastrowid
                c.execute("UPDATE calc_staff_reports_v2 SET status='posted',posted_entry_id=? WHERE id=?",(eid,pr['id']))
            await update.effective_message.reply_text(f"✅ 已入公司账\n👤 {pr['staff']}｜{pr['report_month']}\n工钱 + COM：RM{payable:,.2f}\n🏦 公司资金：RM{calc_balance():,.2f}")
            raise ApplicationHandlerStop
    # whole employee monthly report
    if await save_staff_report(update,text): raise ApplicationHandlerStop
    # settle accumulated U ads: 广告TTL * 3.95 (also UTTL/USDT TTL)
    sm=re.fullmatch(r'(?i)\s*(?:广告|廣告)?\s*(?:U|USD|USDT)?\s*TTL\s*\*\s*([\d.]+)\s*',text)
    if sm:
        rate=D(sm.group(1))
        with db() as c: rows=c.execute("SELECT id,amount FROM calc_entries WHERE chat_id=? AND currency='U' AND settled=0 AND category='广告'",(update.effective_chat.id,)).fetchall()
        total=sum((D(r['amount']) for r in rows),D('0'))
        if total<=0: await update.effective_message.reply_text('📢 目前没有待结算的广告 U。'); raise ApplicationHandlerStop
        rm=money(total*rate)
        with db() as c:
            recdate=datetime.now(TZ).date().isoformat()
            cur=c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date) VALUES(?,?,?,?,?,'RM','广告','广告U结算',?,1,?)",(now_iso(),update.effective_chat.id,update.effective_user.id,'expense',str(rm),text,recdate)); sid=cur.lastrowid
            ids=[r['id'] for r in rows]; c.executemany('UPDATE calc_entries SET settled=1,settlement_id=? WHERE id=?',[(sid,i) for i in ids])
        await update.effective_message.reply_text(f'💱 广告 U 结算\n{money(total):,.2f} U × {rate} = RM{rm:,.2f}\n\n✅ 已从公司资金扣除\n🏦 当前资金：RM{calc_balance():,.2f}')
        raise ApplicationHandlerStop
    # generic multiplication/division: calculation only
    cm=re.fullmatch(r'\s*([\d,.]+)\s*([*/])\s*([\d,.]+)\s*',text)
    if cm:
        a=D(cm.group(1).replace(',','')); b=D(cm.group(3).replace(',',''))
        if cm.group(2)=='/' and b==0: await update.effective_message.reply_text('❌ 不能除以 0'); raise ApplicationHandlerStop
        result=money(a*b if cm.group(2)=='*' else a/b)
        await update.effective_message.reply_text(f'🧮 {a} {cm.group(2)} {b} = {result:,.2f}\n（只计算，不入公司账）'); raise ApplicationHandlerStop
    # Main company quick income with transaction fee.
    # Examples:
    #   +1200       -> normal RM1200 income (handled by the generic +/- parser below)
    #   +1200-1fee  -> Gross RM1200, Fee RM1, Net RM1199
    # A photo, when present, is evidence only and never changes the amount.
    if update.effective_chat.id == VOUCHER_CALC_CHAT_ID:
        fm_quick=re.fullmatch(r'\s*\+\s*([\d,]+(?:\.\d+)?)\s*-\s*([\d,]+(?:\.\d+)?)\s*fee(?:\s+(.*?))?\s*',text,re.I)
        if fm_quick:
            gross=money(fm_quick.group(1).replace(',',''))
            fee=money(fm_quick.group(2).replace(',',''))
            if fee < 0 or fee > gross:
                await update.effective_message.reply_text('❌ Fee 金额不正确。')
                raise ApplicationHandlerStop
            net=money(gross-fee)
            label=clean_label(fm_quick.group(3) or '入账') or '入账'
            bid=f"qfee:{update.effective_chat.id}:{update.effective_message.message_id}"
            recdate=datetime.now(TZ).date().isoformat()
            with db() as c:
                cur=c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,batch_id) VALUES(?,?,?,?,?,'RM',?,?,?,1,?,?)",
                              (now_iso(),update.effective_chat.id,update.effective_user.id,'income',str(gross),label,label,text,recdate,bid))
                eid=cur.lastrowid
                if fee>0:
                    c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date,batch_id) VALUES(?,?,?,?,?,'RM','手续费','Fee',?,1,?,?)",
                              (now_iso(),update.effective_chat.id,update.effective_user.id,'expense',str(fee),text,recdate,bid))
            kb=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ 撤销此笔',callback_data=f'ceundo:{eid}')]])
            proof='\n🖼️ 图片：凭证' if update.effective_message.photo else ''
            msg=await update.effective_message.reply_text(
                f'✅ 已入账\nGross：RM{gross:,.2f}\nFee：RM{fee:,.2f}\nNet：RM{net:,.2f}'
                f'{proof}\n项目：{label}\n余额：RM{calc_balance():,.2f}',reply_markup=kb)
            with db() as c:
                c.execute('INSERT OR REPLACE INTO calc_entry_receipts(entry_id,chat_id,message_id,source_message_id,created_at) VALUES(?,?,?,?,?)',
                          (eid,update.effective_chat.id,msg.message_id,update.effective_message.message_id,now_iso()))
            raise ApplicationHandlerStop

    # + / - ledger, sign can be before amount or immediately before amount after label
    m=re.fullmatch(r'\s*([+-])\s*([\d,]+(?:\.\d+)?)\s*(.*?)\s*',text)
    if not m:
        m=re.fullmatch(r'\s*(.*?)\s*([+-])\s*([\d,]+(?:\.\d+)?)\s*',text)
        if m: sign,amt,label=m.group(2),m.group(3),m.group(1)
    else: sign,amt,label=m.group(1),m.group(2),m.group(3)
    if m:
        amount=money(amt.replace(',','')); label=clean_label(label)
        # U/USD/USDT anywhere => U bucket; only negative U expenses supported as pending conversion
        is_u=bool(re.search(r'(?i)(?:USDT|USD|(?<![A-Z])U(?![A-Z]))',text))
        if is_u:
            label=clean_label(re.sub(r'(?i)USDT|USD|(?<![A-Z])U(?![A-Z])','',label)); category=classify_expense(label)
            kind='income' if sign=='+' else 'expense'
            recdate=datetime.now(TZ).date().isoformat()
            with db() as c: c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date) VALUES(?,?,?,?,?,'U',?,?,?,0,?)",(now_iso(),update.effective_chat.id,update.effective_user.id,kind,str(amount),category,label,text,recdate))
            await update.effective_message.reply_text(f"✅ 已记录 {'收入' if sign=='+' else '支出'}：{amount:,.2f} U｜{label}\n📢 U 先累计，结算汇率后才影响 RM 公司资金。")
        else:
            kind='income' if sign=='+' else 'expense'; category=label if kind=='income' else classify_expense(label)
            recdate=datetime.now(TZ).date().isoformat()
            with db() as c:
                cur=c.execute("INSERT INTO calc_entries(created_at,chat_id,user_id,kind,amount,currency,category,label,raw_text,settled,record_date) VALUES(?,?,?,?,?,'RM',?,?,?,1,?)",(now_iso(),update.effective_chat.id,update.effective_user.id,kind,str(amount),category,label,text,recdate)); eid=cur.lastrowid
            kb=InlineKeyboardMarkup([[InlineKeyboardButton('↩️ 撤销此笔',callback_data=f'ceundo:{eid}')]])
            msg=await update.effective_message.reply_text(f"{'➕ 收入' if sign=='+' else '➖ 支出'}\n金额：RM{amount:,.2f}\n项目：{label}\n余额：RM{calc_balance():,.2f}",reply_markup=kb)
            with db() as c: c.execute('INSERT OR REPLACE INTO calc_entry_receipts(entry_id,chat_id,message_id,source_message_id,created_at) VALUES(?,?,?,?,?)',(eid,update.effective_chat.id,msg.message_id,update.effective_message.message_id,now_iso()))
        raise ApplicationHandlerStop
    # Do not let calculator-group free text leak into Lead routing.
    raise ApplicationHandlerStop

async def calc_private_report_worker(app):
    while True:
        try:
            now=datetime.now(TZ)
            if now.hour==23 and now.minute==59:
                if now.month==12 and now.day==31:
                    jobs=[('year',now.date(),f'year:{now.year}')]
                elif now.day==calendar.monthrange(now.year,now.month)[1]:
                    jobs=[('month',now.date(),f'month:{now:%Y-%m}')]
                elif now.weekday()==6:
                    jobs=[('week',now.date(),f'week:{now.date().isocalendar().year}-{now.date().isocalendar().week}')]
                else:
                    jobs=[('day',now.date(),f'day:{now.date()}')]
                for cid in sorted(calc_chat_ids()):
                    _calc_scope_chat.set(int(cid))
                    for kind,anchor,key0 in jobs:
                        key=f'{cid}:{key0}'
                        with db() as c:
                            exists=c.execute('SELECT 1 FROM calc_report_sent WHERE key=?',(key,)).fetchone()
                        if not exists:
                            await app.bot.send_message(chat_id=OWNER_ID,text=calc_summary(kind,anchor))
                            with db() as c:
                                c.execute('INSERT OR REPLACE INTO calc_report_sent(key,sent_at) VALUES(?,?)',(key,now_iso()))
            await asyncio.sleep(30)
        except Exception:
            LOG.exception('calc private report worker error'); await asyncio.sleep(60)
# ===== END COMPANY CALCULATOR MODULE =====


async def main():
    init_db()
    calc_init_db()
    tg = Application.builder().token(BOT_TOKEN).build()
    # Company calculator: record company-group messages before any router can stop them.
    tg.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r"^\s*/?(?:(?:来过|來過)(?:\s*(?:fb|facebook|tk|tiktok|未注明))?|(?:fb|facebook|tk|tiktok|未注明)\s*(?:来过|來過))\s*$", re.I)), s_came_query_router), group=-1200)
    tg.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r"^\s*/?(?:完整|完整模式|来过完整|來過完整)\s*$", re.I)), s_came_mode_router), group=-1200)
    tg.add_handler(CallbackQueryHandler(s_came_mode_callback, pattern=r'^camefmt:[sf]:\d+$'), group=-1200)
    tg.add_handler(MessageHandler(filters.ALL, calc_message_audit_router), group=-1100)
    tg.add_handler(MessageHandler(filters.ALL, universal_group_id_router), group=-1000)
    tg.add_handler(CommandHandler("calculator_here", calculator_here_cmd), group=-200)
    tg.add_handler(CommandHandler("fund_set", fund_set_cmd), group=-200)
    tg.add_handler(CommandHandler("balance", calc_balance_cmd), group=-200)
    tg.add_handler(CommandHandler("company", calc_company_cmd), group=-200)
    tg.add_handler(CommandHandler("kpi", calc_kpi_cmd), group=-200)
    tg.add_handler(CommandHandler("today", calc_today_cmd), group=-200)
    tg.add_handler(CommandHandler("summary", calc_summary_cmd), group=-200)
    tg.add_handler(CommandHandler("report", calc_report_alias_cmd), group=-200)
    tg.add_handler(CommandHandler("week", calc_week_cmd), group=-200)
    tg.add_handler(CommandHandler("month", calc_month_cmd), group=-200)
    tg.add_handler(CommandHandler("year", calc_year_cmd), group=-200)
    tg.add_handler(CallbackQueryHandler(backup_confirm_callback, pattern=r'^bconfirm:\d+$'), group=-310)
    tg.add_handler(CallbackQueryHandler(backup_confirm_source_callback, pattern=r'^bconfirmsrc:\d+$'), group=-311)
    tg.add_handler(CallbackQueryHandler(cardfee_callback, pattern=r'^cf(?:ok|cancel):\d+$'), group=-201)
    tg.add_handler(CallbackQueryHandler(calc_entry_callback, pattern=r'^ceundo:\d+$'), group=-201)
    tg.add_handler(CallbackQueryHandler(lead_duplicate_confirm_callback, pattern=r'^ld(?:confirm|cancel):\d+$'), group=-1500)
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, calc_date_router), group=-202)
    tg.add_handler(MessageHandler(filters.TEXT, calc_text_router), group=-199)
    # Photo in a calculator group is evidence only; its caption can carry + / - ledger commands.
    tg.add_handler(MessageHandler(filters.PHOTO, calc_text_router), group=-199)
    for cmd, fn in [
             ("start_loan", start_loan), ("start_antiscam", start_antiscam), ("team_loan", team_loan), ("team_antiscam", team_antiscam),              ("find_remove", find_del_cmd), ("find_list", find_users_cmd),              ("find_add", find_add_cmd), ("find_del", find_del_cmd), ("find_users", find_users_cmd),
             ("backup_today", backup_today_cmd), ("template", backup_template_cmd), ("stop_backup", stop_backup_cmd), ("start_backup", start_backup_cmd),   ("help", help_cmd), ("addassistant", add_assistant),
        ("delassistant", del_assistant), ("assistants", assistants),
        ("start_lead", start_lead), ("stop_lead", stop_lead), ("inbox_here", inbox_here), ("inbox", inbox_cmd),
        ("team_here", team_here), ("delteam", del_team), ("teams", teams),
        ("lead", lead_cmd), ("staff", staff_help), ("guide", staff_help), ("report", report), ("week", week_report_cmd), ("month", month_report_cmd), ("schedule", schedule_cmd), ("today", report),
    ]:
        tg.add_handler(CommandHandler(cmd, fn))
    # In 888 only, read find before status (-160) and follow-up replies (-150).
    tg.add_handler(MessageHandler(filters.TEXT, backup_888_find_priority_router), group=-180)
    # Record every meaningful Reply against its exact 888 record. This does
    # not stop the Follow Up/status handlers that run afterward.
    tg.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & filters.REPLY & ~filters.COMMAND,
                                  backup_record_reply_capture_router), group=-175)
    # Flexible find: accepts find / /find with any capitalization.
    tg.add_handler(MessageHandler(filters.TEXT & filters.Regex(re.compile(r"^\s*/?find(?:@\w+)?(?:\s+.+|\s*[:：]\s*.+)?\s*$", re.I)), backup_find_flexible_router), group=-55)
    # FIND permission commands - hard high-priority registration
    tg.add_handler(CommandHandler("find_add", find_add_cmd), group=-50)
    tg.add_handler(CommandHandler("find_del", find_del_cmd), group=-50)
    tg.add_handler(CommandHandler("find_list", find_users_cmd), group=-50)
    tg.add_handler(CommandHandler("find_users", find_users_cmd), group=-50)
    tg.add_handler(CommandHandler("reporttest", report_test_cmd), group=-1)
    tg.add_handler(CommandHandler("start", start), group=-20)
    tg.add_handler(CommandHandler("version", version_cmd), group=-20)
    tg.add_handler(CommandHandler("excel_status", excel_status_cmd), group=-20)
    tg.add_handler(CommandHandler("set_inbox", set_inbox_cmd), group=-20)
    tg.add_handler(CommandHandler("inbox_debug", inbox_debug_cmd), group=-20)
    tg.add_handler(CommandHandler("group_id", group_id_cmd), group=-300)
    # Robust admin-add parser: catches /admin_add <ID> before all route/calculator handlers.
    tg.add_handler(MessageHandler(filters.Regex(r"^/admin_add(?:@\w+)?\s+\d+\s*$"), admin_add_raw_router), group=-299)
    # Owner admin management: register once, before route/calculator handlers.
    tg.add_handler(CommandHandler("admin_add", admin_add_cmd), group=-290)
    tg.add_handler(CommandHandler("admin_remove", admin_remove_cmd), group=-290)
    tg.add_handler(CommandHandler("admin_list", admin_list_cmd), group=-290)
    tg.add_handler(CommandHandler("backup_main", backup_main_cmd), group=-20)
    tg.add_handler(CommandHandler("backup_here", backup_here_cmd), group=-20)
    tg.add_handler(CommandHandler("backup_route", backup_route_cmd), group=-20)
    tg.add_handler(CommandHandler("team_add", team_add_cmd), group=-20)
    tg.add_handler(CommandHandler("teams_p", teams_persistent_cmd), group=-20)
    tg.add_handler(CommandHandler("persist_status", persist_status_cmd), group=-20)
    tg.add_handler(MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, followup_reply_handler), group=-150)
    tg.add_handler(MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, backup_cancel_handler), group=-100)
    tg.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, csv_import_router), group=-90)
    tg.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, backup_auto_router), group=-20)
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lead_text_router), group=-10)
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sos_self_destruct_handler), group=-8)

    tg.add_handler(MessageHandler((filters.PHOTO | filters.TEXT | filters.CAPTION) & ~filters.COMMAND, backup_confirm_prompt_router), group=-4)
    tg.add_handler(MessageHandler((filters.PHOTO | filters.TEXT | filters.CAPTION) & ~filters.COMMAND, backup_direct_router), group=-3)
    tg.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, backup_edited_handler), group=-2)
    tg.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, status_reply_handler), group=-160)

    asyncio.create_task(backup_cleanup_worker(tg if 'tg' in locals() else app))
    asyncio.create_task(followup_worker(tg))
    await tg.initialize()
    await tg.start()
    await tg.updater.start_polling(drop_pending_updates=True)

    api = web.Application()
    api["tg"] = tg
    api.add_routes([web.get("/", health), web.get("/health", health), web.post("/webhook/lead", http_lead)])
    runner = web.AppRunner(api)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    task = asyncio.create_task(daily_report(tg))
    calc_task = asyncio.create_task(calc_private_report_worker(tg))
    excel_task = asyncio.create_task(excel_sync_worker(tg)) if EXCEL_SOURCE_URL else None
    LOG.info("Lead Bot V1 auto-distribute started")
    try:
        await asyncio.Event().wait()
    finally:
        task.cancel()
        if excel_task:
            excel_task.cancel()
        await runner.cleanup()
        await tg.updater.stop()
        await tg.stop()
        await tg.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
