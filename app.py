# ═══════════════════════════════════════════════════════════════
# app.py — SHAPPNO Web Panel (Render Ready + Keep Alive)
# ═══════════════════════════════════════════════════════════════

import os
import time
import threading
import urllib.request
from functools import wraps
from flask import Flask, request, jsonify, session, render_template, redirect, url_for

import database as db
import bot_manager

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "shappno_change_me_2024_secure")

REGIONS = ["BD", "IND", "PK", "SG", "ID", "ME", "VN", "TW", "CIS", "TH", "EU", "US"]


def current_user():
    tok = session.get("token")
    if tok:
        u = db.verify_session(tok)
        if u:
            return u
    if session.get("admin_key_id"):
        return {
            "user_id": session.get("admin_key_id"),
            "username": session.get("admin_username", "Admin"),
            "owner_type": "admin",
            "slots": session.get("admin_slots", 1),
            "expires_at": session.get("admin_expires", 0),
        }
    return None


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login_page"))
        return f(*a, user=u, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin_panel"):
            return redirect(url_for("admin_page"))
        return f(*a, **kw)
    return wrapper


# ═══════════════════════════════════════════════════════════════
# PUBLIC LOGIN
# ═══════════════════════════════════════════════════════════════
@app.route("/")
def index():
    if db.get_setting("website_on", "1") != "1":
        return render_template("login.html", off=True, regions=REGIONS)
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/login")
def login_page():
    off = db.get_setting("website_on", "1") != "1"
    return render_template("login.html", off=off, regions=REGIONS)


@app.route("/api/login", methods=["POST"])
def api_login():
    if db.get_setting("website_on", "1") != "1":
        return jsonify({"ok": False, "msg": "Website is temporarily off"}), 403
    data = request.json or {}
    ident = (data.get("identifier") or "").strip()
    if not ident:
        return jsonify({"ok": False, "msg": "Enter username or password"}), 400
    res = db.public_login(ident)
    if not res:
        return jsonify({"ok": False, "msg": "Invalid"}), 400
    session["token"] = res["token"]
    return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ═══════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════
@app.route("/dashboard")
@login_required
def dashboard(user):
    return render_template("dashboard.html", user=user, regions=REGIONS)


@app.route("/api/me")
@login_required
def api_me(user):
    bots = db.get_bots(user["owner_type"], user["user_id"])
    for b in bots:
        b["running"] = bot_manager.is_running(b["id"])
    return jsonify({"ok": True, "user": user, "bots": bots})


@app.route("/api/bots/add", methods=["POST"])
@login_required
def api_bot_add(user):
    d = request.json or {}
    bot_name = (d.get("bot_name") or "").strip()
    bot_uid = (d.get("bot_uid") or "").strip()
    bot_password = (d.get("bot_password") or "").strip()
    region = (d.get("region") or "").strip()
    display_name = (d.get("display_name") or "").strip() or bot_name
    if not all([bot_name, bot_uid, bot_password, region]):
        return jsonify({"ok": False, "msg": "All fields required"}), 400
    bid, err = db.add_bot(user["owner_type"], user["user_id"],
                          bot_name, bot_uid, bot_password, region, display_name, user["slots"])
    if err:
        return jsonify({"ok": False, "msg": err}), 400
    return jsonify({"ok": True, "bot_id": bid})


@app.route("/api/bots/<int:bid>/<action>", methods=["POST"])
@login_required
def api_bot_action(user, bid, action):
    bot = db.get_bot_by_id(bid)
    if not bot:
        return jsonify({"ok": False, "msg": "Bot not found"}), 404
    if bot["owner_type"] != user["owner_type"] or bot["owner_id"] != user["user_id"]:
        return jsonify({"ok": False, "msg": "Not yours"}), 403
    if action in ("on", "start"):
        bot_manager.start_bot(bid); db.update_bot_running(bid, True)
    elif action in ("off", "stop"):
        bot_manager.stop_bot(bid); db.update_bot_running(bid, False)
    elif action == "restart":
        bot_manager.restart_bot(bid)
    elif action == "delete":
        bot_manager.stop_bot(bid); db.delete_bot(bid)
    else:
        return jsonify({"ok": False, "msg": "Invalid action"}), 400
    return jsonify({"ok": True})


@app.route("/api/bots/<int:bid>/logs")
@login_required
def api_bot_logs(user, bid):
    bot = db.get_bot_by_id(bid)
    if not bot or bot["owner_type"] != user["owner_type"] or bot["owner_id"] != user["user_id"]:
        return jsonify({"ok": False}), 403
    return jsonify({"ok": True, "logs": db.get_logs(bid, 200)})


@app.route("/api/bots/<int:bid>/logs/clear", methods=["POST"])
@login_required
def api_bot_logs_clear(user, bid):
    bot = db.get_bot_by_id(bid)
    if not bot or bot["owner_type"] != user["owner_type"] or bot["owner_id"] != user["user_id"]:
        return jsonify({"ok": False}), 403
    db.clear_logs(bid)
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════
@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/api/settings-login", methods=["POST"])
def api_settings_login():
    d = request.json or {}
    pw = (d.get("password") or "").strip()
    if not pw:
        return jsonify({"ok": False, "msg": "Enter password"}), 400
    res = db.admin_login_session(pw)
    if not res:
        return jsonify({"ok": False, "msg": "Invalid or expired password"}), 401
    session["token"] = res["token"]
    session["admin_key_id"] = res["user_id"]
    session["admin_username"] = res["username"]
    session["admin_slots"] = res["slots"]
    session["admin_expires"] = res["expires_at"]
    return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.route("/settings/logout")
def settings_logout():
    for k in ("token", "admin_key_id", "admin_username", "admin_slots", "admin_expires"):
        session.pop(k, None)
    return redirect(url_for("login_page"))


# ═══════════════════════════════════════════════════════════════
# ADMIN PANEL
# ═══════════════════════════════════════════════════════════════
@app.route("/admin-shappno")
def admin_page():
    return render_template("admin.html")


@app.route("/admin-shappno/login", methods=["POST"])
def admin_login():
    d = request.json or {}
    if d.get("password", "") == db.get_setting("admin_password", "1444444655514444446555"):
        session["admin_panel"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "Wrong password"}), 401


@app.route("/admin-shappno/logout")
def admin_logout():
    session.pop("admin_panel", None)
    return redirect(url_for("admin_page"))


@app.route("/admin-shappno/api/stats")
@admin_required
def admin_api_stats():
    return jsonify({"ok": True, "stats": db.admin_stats(),
                    "website_on": db.get_setting("website_on", "1") == "1"})


@app.route("/admin-shappno/api/website-toggle", methods=["POST"])
@admin_required
def admin_toggle_website():
    cur = db.get_setting("website_on", "1")
    new = "0" if cur == "1" else "1"
    db.set_setting("website_on", new)
    return jsonify({"ok": True, "website_on": new == "1"})


@app.route("/admin-shappno/api/keys")
@admin_required
def admin_api_keys():
    return jsonify({"ok": True, "keys": db.list_admin_keys()})


@app.route("/admin-shappno/api/keys/create", methods=["POST"])
@admin_required
def admin_api_keys_create():
    d = request.json or {}
    try:
        pw = (d.get("password") or "").strip()
        slots = int(d.get("slots", 1))
        hours = int(d.get("duration_hours", 24))
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid numbers"}), 400
    if not pw or slots < 1 or hours < 1:
        return jsonify({"ok": False, "msg": "All fields > 0"}), 400
    return jsonify({"ok": True, "id": db.create_admin_key(pw, slots, hours)})


@app.route("/admin-shappno/api/keys/<int:kid>/toggle", methods=["POST"])
@admin_required
def admin_api_key_toggle(kid):
    db.toggle_admin_key(kid); return jsonify({"ok": True})


@app.route("/admin-shappno/api/keys/<int:kid>/delete", methods=["POST"])
@admin_required
def admin_api_key_delete(kid):
    db.delete_admin_key(kid); return jsonify({"ok": True})


@app.route("/admin-shappno/api/users")
@admin_required
def admin_api_users():
    return jsonify({"ok": True, "users": db.admin_all_users()})


@app.route("/admin-shappno/api/bots")
@admin_required
def admin_api_bots():
    return jsonify({"ok": True, "bots": db.admin_all_bots()})


# ═══════════════════════════════════════════════════════════════
# DANGER ZONE — Purge all public users
# ═══════════════════════════════════════════════════════════════
@app.route("/admin-shappno/api/purge-public", methods=["POST"])
@admin_required
def admin_purge_public():
    d = request.json or {}
    confirm = (d.get("confirm") or "").strip().upper()
    if confirm != "DELETE":
        return jsonify({"ok": False, "msg": "Type DELETE to confirm"}), 400
    # Stop all running public bots
    try:
        for b in db.admin_all_bots():
            if b["owner_type"] == "public":
                try:
                    bot_manager.stop_bot(b["id"])
                except Exception:
                    pass
    except Exception:
        pass
    db.delete_all_public_users()
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════
# HEALTH + PING
# ═══════════════════════════════════════════════════════════════
@app.route("/healthz")
def healthz():
    return {"status": "ok"}, 200


@app.route("/api/ping")
def api_ping():
    return {"ok": True, "ts": int(time.time())}, 200


# ═══════════════════════════════════════════════════════════════
# KEEP ALIVE THREAD
# ═══════════════════════════════════════════════════════════════
def keep_alive_forever():
    while True:
        time.sleep(300)
        try:
            render_url = os.environ.get("RENDER_EXTERNAL_URL")
            if render_url:
                ping_url = f"{render_url}/api/ping"
            else:
                port = os.environ.get("PORT", 8080)
                ping_url = f"http://127.0.0.1:{port}/api/ping"
            req = urllib.request.Request(ping_url, headers={'User-Agent': 'KeepAlive/2.0'})
            urllib.request.urlopen(req, timeout=10)
            print(f"[KeepAlive] ✅ Ping {time.strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"[KeepAlive] ⚠️ {e}")


threading.Thread(target=keep_alive_forever, daemon=True).start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"[*] SHAPPNO starting on {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)