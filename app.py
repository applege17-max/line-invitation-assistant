"""Private LINE notification bridge. Credentials are environment variables only."""
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from invitations import cards, verify_action, apply_label

LOG = logging.getLogger("line-notifier")
MAX_BODY = 128 * 1024
REQUIRED = ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_USER_ID", "NOTIFY_API_KEY")
HELP = "Apple 的 LINE 通知已連線 ✅\n之後可接收工作邀約整理與待辦通知。\n輸入「狀態」或「ping」可測試連線。\n這裡目前只提供通知與連線測試，尚未自動讀取信箱或回覆邀約。"


def line_call(endpoint, payload, retry_key=None):
    headers = {"Authorization": "Bearer " + os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
               "Content-Type": "application/json"}
    if retry_key:
        headers["X-Line-Retry-Key"] = retry_key
    request = urllib.request.Request("https://api.line.me/v2/bot/message/" + endpoint,
                                     data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.headers.get("X-Line-Request-Id")
    except urllib.error.HTTPError as error:
        # LINE returns 409 when a retry key was already accepted.
        if error.code == 409 and retry_key and error.headers.get("X-Line-Accepted-Request-Id"):
            return error.headers.get("X-Line-Accepted-Request-Id")
        LOG.warning("LINE request failed with HTTP %s", error.code)
        raise RuntimeError("LINE rejected the request") from None
    except (urllib.error.URLError, TimeoutError):
        LOG.warning("LINE request timed out or could not connect")
        raise RuntimeError("LINE connection failed") from None


def text_messages(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    # Conservatively count UTF-16 units so emoji also stay within LINE limits.
    chunks, current, units = [], [], 0
    for char in text:
        count = 2 if ord(char) > 0xFFFF else 1
        if units + count > 4500:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += count
    if current:
        chunks.append("".join(current))
    if len(chunks) > 5:
        raise ValueError("text is too long; send at most five message chunks")
    return [{"type": "text", "text": chunk} for chunk in chunks]


class NotificationApp:
    def __init__(self):
        self.seen = OrderedDict()
        self.lock = threading.Lock()

    def response(self, start_response, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        start_response(status, [("Content-Type", "application/json; charset=utf-8"),
                                ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                                ("X-Content-Type-Options", "nosniff")])
        return [body]

    def __call__(self, env, start_response):
        path, method = env.get("PATH_INFO", "/"), env.get("REQUEST_METHOD", "GET")
        if path in ("/", "/healthz") and method == "GET":
            ready = all(os.environ.get(key) for key in REQUIRED)
            return self.response(start_response, "200 OK" if ready else "503 Service Unavailable",
                                 {"service": "apple-line-notifier", "status": "ready" if ready else "not_configured"})
        if path not in ("/webhook", "/api/notify", "/api/invitations"):
            return self.response(start_response, "404 Not Found", {"error": "not_found"})
        if method != "POST":
            return self.response(start_response, "405 Method Not Allowed", {"error": "method_not_allowed"})
        if not all(os.environ.get(key) for key in REQUIRED):
            return self.response(start_response, "503 Service Unavailable", {"error": "not_configured"})
        if path.startswith("/api/"):
            expected = "Bearer " + os.environ["NOTIFY_API_KEY"]
            if not hmac.compare_digest(env.get("HTTP_AUTHORIZATION", "").encode(), expected.encode()):
                return self.response(start_response, "401 Unauthorized", {"error": "unauthorized"})
        try:
            length = int(env.get("CONTENT_LENGTH") or "0")
        except ValueError:
            return self.response(start_response, "400 Bad Request", {"error": "invalid_length"})
        if length < 0 or length > MAX_BODY:
            return self.response(start_response, "413 Payload Too Large", {"error": "body_too_large"})
        raw = env["wsgi.input"].read(length)
        if path == "/webhook":
            signature = base64.b64encode(hmac.new(os.environ["LINE_CHANNEL_SECRET"].encode(), raw,
                                                 hashlib.sha256).digest()).decode()
            if not hmac.compare_digest(signature.encode(), env.get("HTTP_X_LINE_SIGNATURE", "").encode()):
                return self.response(start_response, "401 Unauthorized", {"error": "invalid_signature"})
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if path.startswith("/api/"):
                messages = cards(payload.get("invitations")) if path == "/api/invitations" else text_messages(payload.get("text"))
                # Recipient cannot be supplied by callers: always Apple's configured ID.
                retry_key = env.get("HTTP_X_LINE_RETRY_KEY") or str(uuid.uuid4())
                try:
                    uuid.UUID(retry_key)
                except ValueError:
                    raise ValueError("X-Line-Retry-Key must be a UUID") from None
                request_id = line_call("push", {"to": os.environ["LINE_USER_ID"], "messages": messages}, retry_key)
                return self.response(start_response, "200 OK", {"accepted": True, "request_id": request_id,
                                                              "retry_key": retry_key})
            events = payload.get("events", [])
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise ValueError("events must be an array of objects")
            for event in events:
                self.handle_event(event)
            return self.response(start_response, "200 OK", {"ok": True})
        except (ValueError, TypeError, AttributeError, UnicodeError):
            return self.response(start_response, "400 Bad Request", {"error": "invalid_payload"})
        except RuntimeError:
            return self.response(start_response, "502 Bad Gateway", {"error": "line_unavailable"})

    def handle_event(self, event):
        source = event.get("source") or {}
        if source.get("type") != "user" or source.get("userId") != os.environ["LINE_USER_ID"]:
            return  # Ignore strangers and group chats.
        token = event.get("replyToken")
        if not token:
            return
        message = event.get("message") or {}
        if event.get("type") == "postback":
            try:
                thread_id, label = verify_action(event.get("postback", {}).get("data", ""))
                apply_label(thread_id, label)
                return  # Successful classification sends no LINE reply.
            except (ValueError, KeyError, TypeError):
                text = "分類失敗：這張圖卡已過期或按鈕資料無效，請重新取得圖卡。"
            except RuntimeError as error:
                text = "分類失敗：" + str(error)
        elif event.get("type") == "follow":
            text = HELP
        elif event.get("type") == "message" and message.get("type") == "text":
            command = message.get("text", "").strip().lower()
            text = "連線正常 ✅\nApple 的 LINE 通知機器人正在運作。" if command in ("ping", "狀態", "測試") else HELP
        else:
            return
        event_id = event.get("webhookEventId") or token
        # One Gunicorn worker with threads. Cache is only for recent webhook redelivery.
        with self.lock:
            now = time.monotonic()
            while self.seen and (next(iter(self.seen.values())) < now - 3600 or len(self.seen) > 2048):
                self.seen.popitem(last=False)
            if event_id in self.seen:
                return
            self.seen[event_id] = now
        try:
            line_call("reply", {"replyToken": token, "messages": text_messages(text)})
        except RuntimeError:
            with self.lock:
                self.seen.pop(event_id, None)
            raise


app = NotificationApp()

if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", "8000"))
    with make_server("127.0.0.1", port, app) as server:
        print(f"Local notification bridge listening on port {port}")
        server.serve_forever()
