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
from concurrent.futures import ThreadPoolExecutor
from html import unescape

CATEGORIES = ["付費影音", "團購", "公關品", "旅遊服務體驗", "活動／影片／實體邀請", "長期創作者合作"]
LABELS = {"decline": "幫我婉拒", "quote": "可以報價", "gift": "公關品可收"}
CARD_COLOR = "#FFBDD9"  # Approximate screen conversion of C0 M26 Y15 K0.


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
        {"type": "text", "text": label, "size": "xs", "color": "#654455"},
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
            "header": {"type": "box", "layout": "vertical", "backgroundColor": CARD_COLOR, "contents": [
                {"type": "text", "text": item["category"], "size": "sm", "color": "#4D3440"}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "lg", "backgroundColor": CARD_COLOR, "contents": [
                {"type": "text", "text": item["brand"], "weight": "bold", "size": "xl", "wrap": True, "color": "#4D3440"},
                field("產品摘要", item["brand"] + "｜" + item["summary"]),
                field("購買版位", item["placement"]), field("上線檔期／活動時間", item["schedule"]),
                field("授權需求", item["authorization"])]},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "backgroundColor": CARD_COLOR, "contents": [
                {"type": "button", "height": "sm", "style": "secondary", "color": "#FFFFFF", "action": {
                    "type": "postback", "label": label, "data": action_data(thread_id, action)}}
                for action, label in LABELS.items()] + [
                {"type": "button", "height": "sm", "style": "link", "color": "#4D3440", "action": {"type": "uri", "label": "查看原信",
                    "uri": "https://mail.google.com/mail/u/?authuser=" + urllib.parse.quote(os.environ.get("GMAIL_ACCOUNT_EMAIL", ""), safe="") + "#all/" + thread_id}}]}}))
    ordered = [bubble for _, bubble in sorted(bubbles, key=lambda entry: entry[0])]
    return [{"type": "flex", "altText": f"新工作邀約｜共 {len(items)} 件", "contents": {
        "type": "carousel", "contents": ordered[i:i+10]}} for i in range(0, len(ordered), 10)]


def gmail_client():
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
        return gmail
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Gmail 分類失敗，請檢查帳號權限或稍後再試") from None


def apply_label(thread_id, label_name):
    gmail = gmail_client()
    try:
        labels = {label["name"]: label["id"] for label in gmail("labels").get("labels", [])}
        if label_name not in labels:
            raise RuntimeError("信箱找不到「" + label_name + "」標籤")
        gmail("threads/" + thread_id + "/modify", {"addLabelIds": [labels[label_name]]})
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("Gmail 分類失敗，請檢查帳號權限或稍後再試") from None


def decode_body(part):
    if part.get("mimeType") in ("text/plain", "text/html") and part.get("body", {}).get("data"):
        text = base64.urlsafe_b64decode(part["body"]["data"] + "===").decode(errors="replace")
        if part.get("mimeType") == "text/html":
            text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
            text = unescape(re.sub(r"<[^>]+>", " ", text))
        return text
    children = part.get("parts", [])
    plain = [decode_body(child) for child in children if child.get("mimeType") != "text/html"]
    return "\n".join(plain) if any(plain) else "\n".join(decode_body(child) for child in children)


def extracted_card(message):
    """Conservative source excerpts for the immediate unread query; no invented terms."""
    headers = {h["name"].lower(): h["value"] for h in message["payload"].get("headers", [])}
    subject = headers.get("subject", "未提供主旨")
    text = decode_body(message["payload"])
    lines = [re.sub(r"\s+", " ", line).strip(" >*\t") for line in text.splitlines()]
    lines = [line for line in lines if line and not re.match(r"https?://|\[image:|\[cid:", line)]
    lower = (subject + "\n" + text).lower()
    if re.search(r"大使|ambassador|長期.*合作|年度.*合作", lower):
        category = "長期創作者合作"
    elif re.search(r"團購|開團|分潤|affiliate", lower):
        category = "團購"
    elif re.search(r"旅遊|旅行|旅宿|住宿|行程體驗|療程|探訪", lower):
        category = "旅遊服務體驗"
    elif re.search(r"試片|觀賞|觀劇|開幕|出席|首映|活動邀", lower):
        category = "活動／影片／實體邀請"
    elif re.search(r"公關品|gifting|贈禮|免費.*(?:體驗|產品)", lower) and not re.search(r"報價|付費|稿費|合作費用", lower):
        category = "公關品"
    else:
        category = "付費影音"
    match = re.search(r"(?:合作品牌|品牌名稱|品牌)[：:】\s]+([^\n\r]{2,70})", text)
    brand = match.group(1).strip(" *【】") if match else "品牌待確認"
    # Known names are source-grounded only when present in this message.
    names = [name for name in ("DJI", "RE4DAY", "KKLUE", "RENPHO", "ThetaWave", "雄獅旅遊", "niko and ...", "HUROM", "SwitchBot", "GOELIA", "SENSE OF PLACE", "LAMPSI", "PRADA", "PHILIPS", "aircolor") if name.lower() in lower]
    if names:
        brand = "／".join(names)
    def select(pattern, fallback):
        found = [line for line in lines if re.search(pattern, line, flags=re.I)]
        return "；".join(found[:3])[:380] or fallback
    return {"thread_id": message["threadId"], "category": category, "brand": brand[:100],
        "summary": select(r"合作產品|推廣產品|合作商品|主打|產品名稱|商品名稱", subject)[:350],
        "placement": select(r"reels|youtube|\byt\b|threads|tiktok|限動|限時動態|影片置入|合作形式|合作方式", "未提供明確版位，待確認。"),
        "schedule": select(r"(?:上線|曝光|開團|交稿|合作檔期|活動時間|時間：|時間:).*(?:\d|月底|月初)|\d{1,2}[月/]\d{1,2}", "未提供，待確認。"),
        "authorization": select(r"授權|投廣|投放|重製", "信中未提供。")}


def unread_cards():
    gmail = gmail_client()
    query = 'is:unread -in:sent -in:drafts -in:trash -in:spam (邀約 OR 合作邀請 OR collaboration OR sponsorship OR gifting OR invitation OR 媒體試片 OR 敬邀 OR 團購)'
    try:
        listing = gmail("messages?" + urllib.parse.urlencode({"q": query, "maxResults": 50}))
        candidates, seen = [], set()
        for entry in listing.get("messages", []):
            if entry["threadId"] not in seen:
                candidates.append(entry)
                seen.add(entry["threadId"])
        selected = candidates[:20]
        if not selected:
            return [{"type": "text", "text": "目前沒有符合邀約搜尋條件的未讀信件。"}]
        with ThreadPoolExecutor(max_workers=6) as pool:
            messages = list(pool.map(lambda entry: gmail("messages/" + entry["id"] + "?format=full"), selected))
        items = [extracted_card(message) for message in messages]
        note = f"未讀邀約｜本次列出 {len(items)} 件。\n以下為信件原文擷取，品牌或條件未明示時會標成待確認。"
        if listing.get("nextPageToken") or len(candidates) > 20:
            note += "\n尚有較早的未讀信件；本次先列最近 20 件。"
        return [{"type": "text", "text": note}] + cards(items)
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("未讀邀約查詢失敗，請稍後再試") from None
