"""LINE Flex invitation cards and signed Gmail classification actions."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import urllib.request

CATEGORIES = ["付費影音", "團購", "公關品", "旅遊服務體驗", "活動／影片／實體邀請", "長期創作者合作"]
LABELS = {"decline": "幫我婉拒", "quote": "可以報價", "gift": "公關品可收"}


def action_data(thread_id, action):
    expires = str(int(time.time()) + 90 * 86400)
    data = f"{thread_id}:{action}:{expires}"
    signature = hmac.new(os.environ["NOTIFY_API_KEY"].encode(), data.encode(), hashlib.sha256).hexdigest()[:32]
    return urllib.parse.urlencode({"t": thread_id, "a": action, "e": expires, "s": signature})


def verify_action(data):
    fields = urllib.parse.parse_qs(data, strict_parsing=True)
    thread_id, action, expires, signature = (fields[k][0] for k in ("t", "a", "e", "s"))
    if not re.fullmatch(r"[0-9a-f]{10,32}", thread_id) or action not in LABELS:
        raise ValueError("invalid action")
    if int(expires) < time.time():
        raise ValueError("expired action")
    expected = hmac.new(os.environ["NOTIFY_API_KEY"].encode(), f"{thread_id}:{action}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid signature")
    return thread_id, LABELS[action]


def field(label, value):
    return {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [
        {"type": "text", "text": label, "size": "xs", "color": "#7B8090"},
        {"type": "text", "text": value, "size": "sm", "wrap": True, "color": "#222837"}]}


def cards(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValueError("Provide 1 to 50 invitations")
    bubbles = []
    for item in items:
        if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
            raise ValueError("invalid category")
        for key in ("brand", "summary", "placement", "schedule", "authorization"):
            if not isinstance(item.get(key), str) or not item[key].strip() or len(item[key]) > 700:
                raise ValueError("invalid card field")
        thread_id = item.get("thread_id", "")
        if not isinstance(thread_id, str) or not re.fullmatch(r"[0-9a-f]{10,32}", thread_id):
            raise ValueError("invalid thread id")
        bubbles.append((CATEGORIES.index(item["category"]), {
            "type": "bubble", "size": "mega",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#304D49", "contents": [
                {"type": "text", "text": item["category"], "size": "sm", "color": "#FFFFFF"}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "lg", "contents": [
                {"type": "text", "text": item["brand"], "weight": "bold", "size": "xl", "wrap": True},
                field("產品摘要", item["brand"] + "｜" + item["summary"]),
                field("購買版位", item["placement"]), field("上線檔期／活動時間", item["schedule"]),
                field("授權需求", item["authorization"])]},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                {"type": "button", "height": "sm", "style": "primary", "color": "#304D49", "action": {
                    "type": "postback", "label": label, "data": action_data(thread_id, action)}}
                for action, label in LABELS.items()] + [
                {"type": "button", "height": "sm", "style": "link", "action": {"type": "uri", "label": "查看原信",
                    "uri": "https://mail.google.com/mail/u/?authuser=" + urllib.parse.quote(os.environ.get("GMAIL_ACCOUNT_EMAIL", ""), safe="") + "#all/" + thread_id}}]}}))
    ordered = [bubble for _, bubble in sorted(bubbles, key=lambda entry: entry[0])]
    return [{"type": "flex", "altText": f"新工作邀約｜共 {len(items)} 件", "contents": {
        "type": "carousel", "contents": ordered[i:i+10]}} for i in range(0, len(ordered), 10)]


def apply_label(thread_id, label_name):
    raw = os.environ.get("GMAIL_CREDENTIALS_JSON")
    if not raw:
        raise RuntimeError("Gmail 標籤權限尚未完成連接")
    try:
        credentials = json.loads(raw)
        if credentials.get("account_email") != os.environ.get("GMAIL_ACCOUNT_EMAIL"):
            raise ValueError("wrong account")
        body = urllib.parse.urlencode({"client_id": credentials["client_id"], "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"], "grant_type": "refresh_token"}).encode()
        request = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
        with urllib.request.urlopen(request, timeout=12) as response:
            token = json.load(response)["access_token"]
        def gmail(path, payload=None):
            request = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/" + path,
                data=None if payload is None else json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=12) as response:
                return json.load(response)
        if gmail("profile")["emailAddress"].lower() != os.environ.get("GMAIL_ACCOUNT_EMAIL", "").lower():
            raise ValueError("wrong account")
        labels = {label["name"]: label["id"] for label in gmail("labels").get("labels", [])}
        if label_name not in labels:
            raise RuntimeError("信箱找不到「" + label_name + "」標籤")
        gmail("threads/" + thread_id + "/modify", {"addLabelIds": [labels[label_name]]})
        # Add only: preserve existing Gmail labels, inbox state, and unread state.
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Gmail 分類失敗，請檢查帳號權限或稍後再試") from None
