# ═══════════════════════════════════════════════════════════════
# bot_manager.py — Multi-bot lifecycle manager (Render-safe v3)
# ═══════════════════════════════════════════════════════════════

import asyncio
import threading
import time
import traceback
import sys

import login_engine
import bot_core
from database import add_log, update_bot_status, get_bot

print("=" * 60, flush=True)
print("[bot_manager] MODULE LOADING... v3", flush=True)
print("=" * 60, flush=True)

BOTS = {}
_BOTS_LOCK = threading.Lock()

_LOOP = asyncio.new_event_loop()
_LOOP_THREAD = threading.Thread(target=_LOOP.run_forever, daemon=True)
_LOOP_THREAD.start()
print("[bot_manager] Event loop thread started", flush=True)


def _get_loop():
    return _LOOP


def _safe_log(bot_id, text):
    """Never let log failure crash the bot"""
    try:
        add_log(bot_id, text)
    except Exception as e:
        print(f"[_safe_log fail] {e}", flush=True)
    print(f"[bot {bot_id}] {text}", flush=True)


def _safe_status(bot_id, status, running):
    try:
        update_bot_status(bot_id, status, running)
    except Exception as e:
        print(f"[_safe_status fail] {e}", flush=True)


class BotRunner:
    def __init__(self, bot_id, bot_uid, bot_password, region, display_name):
        self.bot_id = bot_id
        self.bot_uid = bot_uid
        self.bot_password = bot_password
        self.region = region
        self.display_name = display_name
        self.task = None
        self.running = False

    def start(self):
        if self.running:
            print(f"[BotRunner {self.bot_id}] already running", flush=True)
            return
        self.running = True
        print(f"[BotRunner {self.bot_id}] START called", flush=True)
        self.task = asyncio.run_coroutine_threadsafe(self._run(), _get_loop())
        _safe_status(self.bot_id, "on", 1)
        _safe_log(self.bot_id, f"[+] Bot starting... ({self.display_name})")

    def stop(self):
        print(f"[BotRunner {self.bot_id}] STOP called", flush=True)
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
        _safe_status(self.bot_id, "off", 0)
        _safe_log(self.bot_id, "[-] Bot stopped")

    async def _run(self):
        print(f"[BotRunner {self.bot_id}] _run() STARTED", flush=True)
        _safe_log(self.bot_id, "[*] Runner started")

        attempt = 0
        while self.running:
            attempt += 1
            try:
                print(f"[BotRunner {self.bot_id}] attempt #{attempt}", flush=True)
                _safe_log(self.bot_id, f"[*] Logging in... (attempt {attempt})")

                # ─── LOGIN ───
                try:
                    result = await asyncio.wait_for(
                        login_engine.do_login(
                            self.bot_uid, self.bot_password, self.region
                        ),
                        timeout=60
                    )
                except asyncio.TimeoutError:
                    _safe_log(self.bot_id, "[-] Login timeout (60s), retry in 10s")
                    await asyncio.sleep(10)
                    continue
                except Exception as le:
                    _safe_log(self.bot_id, f"[-] Login exception: {le}, retry in 10s")
                    print(f"[LOGIN EXCEPTION] {traceback.format_exc()}", flush=True)
                    await asyncio.sleep(10)
                    continue

                if not result:
                    _safe_log(self.bot_id, "[-] Login failed (no result), retry in 15s")
                    await asyncio.sleep(15)
                    continue

                # ─── SUCCESS ───
                _safe_log(self.bot_id, f"[+] Logged in as {result['account_name']} (UID {result['bot_uid']})")

                bot_core.bot_uid = result["bot_uid"]
                bot_core.ToKen = result["token"]
                bot_core.region = result["region"]
                bot_core.DISPLAY_NAME = self.display_name

                key = result["key"]
                iv = result["iv"]
                ts = result["timestamp"]

                ChatAutH = bot_core.create_auth_token_chat(
                    int(result["bot_uid"]), result["token"], int(ts), key, iv
                )
                OnlineAutH, _ = bot_core.build_ob55_online_auth(
                    int(result["bot_uid"]), result["token"], int(ts), key, iv
                )

                # ─── CHAT ───
                _safe_log(self.bot_id, f"[*] Connecting chat {result['chat_ip']}:{result['chat_port']}...")
                ready = asyncio.Event()
                t1 = asyncio.create_task(
                    bot_core.TcPChaT(
                        result["chat_ip"], result["chat_port"],
                        ChatAutH, key, iv, ready, result["region"]
                    )
                )
                try:
                    await asyncio.wait_for(ready.wait(), timeout=20)
                except asyncio.TimeoutError:
                    _safe_log(self.bot_id, "[-] Chat timeout (20s), retry in 5s")
                    t1.cancel()
                    await asyncio.sleep(5)
                    continue

                # ─── ONLINE ───
                _safe_log(self.bot_id, f"[*] Connecting online {result['online_ip']}:{result['online_port']}...")
                await asyncio.sleep(1)
                t2 = asyncio.create_task(
                    bot_core.TcPOnLine(
                        result["online_ip"], result["online_port"],
                        key, iv, OnlineAutH
                    )
                )
                _safe_log(self.bot_id, "[+] ✅ BOT IS ONLINE!")

                await asyncio.gather(t1, t2)

            except asyncio.CancelledError:
                _safe_log(self.bot_id, "[-] Cancelled")
                print(f"[BotRunner {self.bot_id}] CANCELLED", flush=True)
                break
            except Exception as e:
                tb = traceback.format_exc()
                print(f"[BotRunner {self.bot_id}] EXCEPTION:\n{tb}", flush=True)
                _safe_log(self.bot_id, f"[!] Error: {e} -> retry in 10s")
                await asyncio.sleep(10)

        self.running = False
        _safe_status(self.bot_id, "off", 0)
        print(f"[BotRunner {self.bot_id}] _run() EXITED", flush=True)


def start_bot(bot_id):
    print(f"[start_bot] called for bot_id={bot_id}", flush=True)
    with _BOTS_LOCK:
        if bot_id in BOTS and BOTS[bot_id].running:
            print(f"[start_bot] bot {bot_id} already running", flush=True)
            return False
        bot = get_bot(bot_id)
        if not bot:
            print(f"[start_bot] bot {bot_id} NOT FOUND in DB", flush=True)
            return False
        print(f"[start_bot] creating runner for bot {bot_id}", flush=True)
        runner = BotRunner(
            bot_id, bot["bot_uid"], bot["bot_password"],
            bot["region"], bot["display_name"]
        )
        BOTS[bot_id] = runner
        runner.start()
        return True


def stop_bot(bot_id):
    print(f"[stop_bot] called for bot_id={bot_id}", flush=True)
    with _BOTS_LOCK:
        if bot_id in BOTS:
            BOTS[bot_id].stop()
            del BOTS[bot_id]
            return True
    return False


def restart_bot(bot_id):
    stop_bot(bot_id)
    time.sleep(1)
    return start_bot(bot_id)


def is_running(bot_id):
    with _BOTS_LOCK:
        return bot_id in BOTS and BOTS[bot_id].running


print("[bot_manager] MODULE LOADED OK ✅", flush=True)