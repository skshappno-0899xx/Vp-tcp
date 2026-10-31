# ═══════════════════════════════════════════════════════════════
# bot_manager.py — Multi-bot lifecycle manager (Render-safe)
# ═══════════════════════════════════════════════════════════════

import asyncio
import threading
import time
import traceback

import login_engine
import bot_core
from database import add_log, update_bot_status, get_bot

# ─── Global registry ───
BOTS = {}
_BOTS_LOCK = threading.Lock()

# Dedicated event loop thread for ALL bots
_LOOP = asyncio.new_event_loop()
_LOOP_THREAD = threading.Thread(target=_LOOP.run_forever, daemon=True)
_LOOP_THREAD.start()


def _get_loop():
    return _LOOP


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
            return
        self.running = True
        self.task = asyncio.run_coroutine_threadsafe(self._run(), _get_loop())
        update_bot_status(self.bot_id, "on", 1)
        add_log(self.bot_id, f"[+] Bot starting... ({self.display_name})")

    def stop(self):
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
        update_bot_status(self.bot_id, "off", 0)
        add_log(self.bot_id, "[-] Bot stopped")

    async def _run(self):
        while self.running:
            try:
                add_log(self.bot_id, "[*] Logging in...")
                result = await login_engine.do_login(
                    self.bot_uid, self.bot_password, self.region
                )
                if not result:
                    add_log(self.bot_id, "[-] Login failed, retry in 15s")
                    await asyncio.sleep(15)
                    continue

                add_log(self.bot_id, f"[+] Logged in as {result['account_name']} (UID {result['bot_uid']})")
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

                add_log(self.bot_id, f"[*] Connecting chat {result['chat_ip']}:{result['chat_port']}...")
                ready = asyncio.Event()
                t1 = asyncio.create_task(
                    bot_core.TcPChaT(result["chat_ip"], result["chat_port"], ChatAutH, key, iv, ready, result["region"])
                )
                try:
                    await asyncio.wait_for(ready.wait(), timeout=15)
                except asyncio.TimeoutError:
                    add_log(self.bot_id, "[-] Chat connect timeout, retry...")
                    t1.cancel()
                    await asyncio.sleep(5)
                    continue

                add_log(self.bot_id, f"[*] Connecting online {result['online_ip']}:{result['online_port']}...")
                await asyncio.sleep(1)
                t2 = asyncio.create_task(
                    bot_core.TcPOnLine(result["online_ip"], result["online_port"], key, iv, OnlineAutH)
                )
                add_log(self.bot_id, "[+] TCP connected, bot is online ✅")
                await asyncio.gather(t1, t2)

            except asyncio.CancelledError:
                add_log(self.bot_id, "[-] Bot cancelled")
                break
            except Exception as e:
                tb = traceback.format_exc()
                add_log(self.bot_id, f"[!] Error: {e} -> restart in 10s")
                print(f"[BotRunner {self.bot_id}] {tb}")
                await asyncio.sleep(10)

        self.running = False
        update_bot_status(self.bot_id, "off", 0)


def start_bot(bot_id):
    with _BOTS_LOCK:
        if bot_id in BOTS and BOTS[bot_id].running:
            return False
        bot = get_bot(bot_id)
        if not bot:
            return False
        runner = BotRunner(
            bot_id, bot["bot_uid"], bot["bot_password"],
            bot["region"], bot["display_name"]
        )
        BOTS[bot_id] = runner
        runner.start()
        return True


def stop_bot(bot_id):
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