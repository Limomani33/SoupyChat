import os
import json
import threading
import time
import requests
from aiohttp import web

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MAX_MESSAGES = 15
MAX_CHARS    = 300

# ======================
# HTTP: serve index.html
# ======================
async def index(request):
    return web.FileResponse(os.path.join(BASE_DIR, "index.html"))

# ======================
# WEBSOCKET
# ======================
clients  = set()
users    = {}
messages = []

async def websocket_handler(request):
    ws = web.WebSocketResponse(max_msg_size=10_000_000)
    await ws.prepare(request)
    clients.add(ws)
    await ws.send_json({"type": "history", "messages": messages})
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data["type"] == "join":
                    users[ws] = data["name"]
                elif data["type"] == "message":
                    content = data.get("content", "")
                    if len(content) > MAX_CHARS:
                        await ws.send_json({
                            "type": "error",
                            "content": f"Message too long! Max {MAX_CHARS} characters."
                        })
                        continue
                    payload = {
                        "type": "message",
                        "name": users.get(ws, "Anonymous"),
                        "content": content
                    }
                    messages.append(payload)
                    while len(messages) > MAX_MESSAGES:
                        messages.pop(0)
                    for client in clients:
                        await client.send_json(payload)
                elif data["type"] in ("image", "audio"):
                    payload = {
                        "type": data["type"],
                        "name": users.get(ws, "Anonymous"),
                        "content": data["content"]
                    }
                    messages.append(payload)
                    while len(messages) > MAX_MESSAGES:
                        messages.pop(0)
                    for client in clients:
                        await client.send_json(payload)

                elif data["type"] == "dev_kick":
                    dev_phid = data.get("dev_phid", "")
                    target   = data.get("target", "")
                    # Verify with PHID server
                    try:
                        r = requests.post(
                            "https://phid-accpanel.onrender.com/dev/kick",
                            json={"dev_phid": dev_phid, "target": target},
                            timeout=5
                        )
                        result = r.json()
                        if result.get("ok"):
                            # Find and disconnect the target
                            for client, name in list(users.items()):
                                if name == target:
                                    await client.send_json({"type": "dev_kicked"})
                                    await client.close()
                    except Exception:
                        pass

                elif data["type"] == "dev_settings":
                    dev_phid  = data.get("dev_phid", "")
                    max_chars = data.get("max_chars", 300)
                    max_msgs  = data.get("max_msgs", 15)
                    # Verify dev phid
                    try:
                        r = requests.get(
                            f"https://phid-accpanel.onrender.com/dev/check/{dev_phid}",
                            timeout=5
                        )
                        # Simple check — if endpoint returns any valid response treat as ok
                        # (real check is dev_phid format starts with DEVPHID-)
                        if dev_phid.startswith("DEVPHID-"):
                            global MAX_CHARS, MAX_MESSAGES
                            MAX_CHARS    = max(10, min(2000, int(max_chars)))
                            MAX_MESSAGES = max(1,  min(200,  int(max_msgs)))
                            update = {"type": "settings_upd", "max_chars": MAX_CHARS, "max_msgs": MAX_MESSAGES}
                            for client in clients:
                                await client.send_json(update)
                    except Exception:
                        pass
    finally:
        clients.discard(ws)
        users.pop(ws, None)
    return ws

# ======================
# APP SETUP
# ======================
app = web.Application()
app.router.add_get("/",   index)
app.router.add_get("/ws", websocket_handler)

# ======================
# KEEP ALIVE
# ======================
SELF_URL = os.environ.get("SELF_URL", "")

def keep_alive():
    while True:
        try:
            if SELF_URL:
                requests.get(SELF_URL, timeout=10)
            requests.get("https://phid-accpanel.onrender.com", timeout=10)
        except Exception:
            pass
        time.sleep(360)

threading.Thread(target=keep_alive, daemon=True).start()

port = int(os.environ.get("PORT", 8000))
web.run_app(app, host="0.0.0.0", port=port)
