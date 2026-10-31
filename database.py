# ═══════════════════════════════════════════════════════════════
# database.py — SHAPPNO SQLite DB (Render-ready)
# ═══════════════════════════════════════════════════════════════

import os
import sqlite3
import hashlib
import secrets
import time

DB_PATH = os.environ.get("DB_PATH", "shappno.db")

_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    try:
        os.makedirs(_db_dir, exist_ok=True)
    except Exception:
        pass


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            password TEXT NOT NULL,
            slots INTEGER NOT NULL DEFAULT 1,
            duration_hours INTEGER NOT NULL DEFAULT 24,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS public_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            login_time INTEGER,
            session_token TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_type TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            bot_name TEXT NOT NULL,
            bot_uid TEXT NOT NULL,
            bot_password TEXT NOT NULL,
            region TEXT NOT NULL,
            display_name TEXT NOT NULL,
            running INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            log_text TEXT,
            timestamp INTEGER NOT NULL
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner_type, owner_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_logs_bot ON bot_logs(bot_id)")

    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('website_on', '1')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', '1444444655514444446555')")

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════
def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# ADMIN KEYS
# ═══════════════════════════════════════════════════════════════
def create_admin_key(password, slots, duration_hours):
    now = int(time.time())
    expires = now + duration_hours * 3600
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO admin_keys (password, slots, duration_hours, created_at, expires_at, active) VALUES (?,?,?,?,?,1)",
        (password, slots, duration_hours, now, expires)
    )
    conn.commit()
    kid = c.lastrowid
    conn.close()
    return kid


def verify_admin_key(password):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM admin_keys WHERE password=? AND active=1", (password,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if int(time.time()) > row["expires_at"]:
        return None
    return dict(row)


def list_admin_keys():
    conn = get_db()
    rows = conn.execute("SELECT * FROM admin_keys ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def toggle_admin_key(kid):
    conn = get_db()
    row = conn.execute("SELECT active FROM admin_keys WHERE id=?", (kid,)).fetchone()
    if row:
        conn.execute("UPDATE admin_keys SET active=? WHERE id=?", (0 if row["active"] else 1, kid))
        conn.commit()
    conn.close()


def delete_admin_key(kid):
    conn = get_db()
    conn.execute("DELETE FROM admin_keys WHERE id=?", (kid,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# PUBLIC USERS
# ═══════════════════════════════════════════════════════════════
def public_login(identifier):
    ident = identifier.strip()
    if not ident:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM public_users WHERE username=?", (ident,)).fetchone()
    if not user:
        try:
            conn.execute("INSERT INTO public_users (username, login_time) VALUES (?, ?)", (ident, int(time.time())))
            conn.commit()
            user = conn.execute("SELECT * FROM public_users WHERE username=?", (ident,)).fetchone()
        except sqlite3.IntegrityError:
            user = conn.execute("SELECT * FROM public_users WHERE username=?", (ident,)).fetchone()

    token = secrets.token_hex(24)
    conn.execute(
        "UPDATE public_users SET session_token=?, login_time=? WHERE id=?",
        (token, int(time.time()), user["id"])
    )
    conn.commit()
    conn.close()
    return {"token": token, "user_id": user["id"], "username": user["username"],
            "owner_type": "public", "slots": 1}


def admin_login_session(password):
    key = verify_admin_key(password)
    if not key:
        return None
    token = secrets.token_hex(24)
    return {
        "token": token,
        "user_id": key["id"],
        "username": f"Admin#{key['id']}",
        "owner_type": "admin",
        "slots": key["slots"],
        "expires_at": key["expires_at"],
    }


def verify_session(token):
    if not token:
        return None
    conn = get_db()
    u = conn.execute("SELECT * FROM public_users WHERE session_token=?", (token,)).fetchone()
    conn.close()
    if u:
        return {"user_id": u["id"], "username": u["username"],
                "owner_type": "public", "slots": 1}
    return None


# ═══════════════════════════════════════════════════════════════
# BOTS
# ═══════════════════════════════════════════════════════════════
def add_bot(owner_type, owner_id, bot_name, bot_uid, bot_password, region, display_name, max_slots):
    conn = get_db()
    c = conn.cursor()
    count = c.execute(
        "SELECT COUNT(*) AS c FROM bots WHERE owner_type=? AND owner_id=?",
        (owner_type, owner_id)
    ).fetchone()["c"]
    if count >= max_slots:
        conn.close()
        return None, f"Slot limit reached ({max_slots})"
    c.execute(
        "INSERT INTO bots (owner_type, owner_id, bot_name, bot_uid, bot_password, region, display_name, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (owner_type, owner_id, bot_name, bot_uid, bot_password, region, display_name, int(time.time()))
    )
    conn.commit()
    bid = c.lastrowid
    conn.close()
    return bid, None


def get_bots(owner_type, owner_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM bots WHERE owner_type=? AND owner_id=? ORDER BY id DESC",
        (owner_type, owner_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_by_id(bot_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def update_bot_running(bot_id, running):
    conn = get_db()
    conn.execute("UPDATE bots SET running=? WHERE id=?", (1 if running else 0, bot_id))
    conn.commit()
    conn.close()


def delete_bot(bot_id):
    conn = get_db()
    conn.execute("DELETE FROM bot_logs WHERE bot_id=?", (bot_id,))
    conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════
def add_log(bot_id, text):
    conn = get_db()
    conn.execute("INSERT INTO bot_logs (bot_id, log_text, timestamp) VALUES (?,?,?)",
                 (bot_id, text, int(time.time())))
    conn.execute("""DELETE FROM bot_logs WHERE bot_id=? AND id NOT IN (
        SELECT id FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT 400)""", (bot_id, bot_id))
    conn.commit()
    conn.close()


def get_logs(bot_id, limit=200):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM bot_logs WHERE bot_id=? ORDER BY id DESC LIMIT ?",
        (bot_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def clear_logs(bot_id):
    conn = get_db()
    conn.execute("DELETE FROM bot_logs WHERE bot_id=?", (bot_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# ADMIN STATS
# ═══════════════════════════════════════════════════════════════
def admin_stats():
    conn = get_db()
    users = conn.execute("SELECT COUNT(*) AS c FROM public_users").fetchone()["c"]
    bots = conn.execute("SELECT COUNT(*) AS c FROM bots").fetchone()["c"]
    running = conn.execute("SELECT COUNT(*) AS c FROM bots WHERE running=1").fetchone()["c"]
    keys = conn.execute("SELECT COUNT(*) AS c FROM admin_keys").fetchone()["c"]
    conn.close()
    return {"users": users, "bots": bots, "running": running, "keys": keys}


def admin_all_users():
    conn = get_db()
    rows = conn.execute("""
        SELECT u.*, 
               (SELECT COUNT(*) FROM bots b WHERE b.owner_type='public' AND b.owner_id=u.id) AS bot_count,
               (SELECT COUNT(*) FROM bots b WHERE b.owner_type='public' AND b.owner_id=u.id AND b.running=1) AS running_count
        FROM public_users u ORDER BY u.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def admin_all_bots():
    conn = get_db()
    rows = conn.execute("SELECT * FROM bots ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
# DELETE ALL PUBLIC USERS (Danger Zone)
# ═══════════════════════════════════════════════════════════════
def delete_all_public_users():
    conn = get_db()
    conn.execute("""
        DELETE FROM bot_logs WHERE bot_id IN (
            SELECT id FROM bots WHERE owner_type='public'
        )
    """)
    conn.execute("DELETE FROM bots WHERE owner_type='public'")
    conn.execute("DELETE FROM public_users")
    conn.commit()
    conn.close()
    return True


# ═══════════════════════════════════════════════════════════════
# BACKWARD COMPAT ALIASES
# ═══════════════════════════════════════════════════════════════
def get_bot(bot_id):
    return get_bot_by_id(bot_id)


def update_bot_status(bot_id, status, running):
    update_bot_running(bot_id, running)


# ═══════════════════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════════════════
init_db()