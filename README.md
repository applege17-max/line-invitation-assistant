# Apple LINE 通知機器人

將經授權的工作邀約摘要與待辦通知傳到固定的 LINE 使用者。Gmail OAuth 憑證與私人信件不包含在本專案，每日讀信與摘要由 Codex 排程處理，服務負責推播與分類按鈕；不提供通用 AI 對話功能。

## 部署

Render Python Web Service，免費方案，Singapore 區域。

- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`
- Health: `/healthz`
- 環境變數：`LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_USER_ID`、`NOTIFY_API_KEY`
- LINE Developers Webhook URL: `https://服務網址/webhook`，驗證後開啟 Use webhook。

機器人只回覆指定使用者的一對一訊息，驗證 LINE 原始請求的 HMAC 簽章。支援「ping」「狀態」「測試」及「業配／未讀邀約／新邀約」。業配查詢列出最近最多 20 個未讀邀約討論串，從原文擷取資料，未提供的欄位標示未提供；不標記已讀。其他文字回覆使用說明。

## 推播

`POST /api/notify`，Header: `Authorization: Bearer <NOTIFY_API_KEY>`，JSON: `{"text":"通知內容"}`。
收件人固定為環境變數的 LINE_USER_ID，不接受呼叫端指定其他收件人。
可附 `X-Line-Retry-Key` UUID；同一通知重試時沿用，避免 LINE 重複接收。

本機 `.env` 設定 `NOTIFY_BASE_URL` 與 `NOTIFY_API_KEY` 後：

```sh
python3 send_notice.py --file /absolute/path/notice.txt
```

HTTP 200 僅表示 LINE 接受請求，不代表使用者已讀。使用者需加入官方帳號好友，且未封鎖帳號。

## 驗證與限制

```sh
python3 -m unittest -v
```

Render 免費服務會在閒置後休眠，首次請求可能較慢。Webhook 最近事件去重存在單一 worker 記憶體內，重啟即清空；不是持久佇列。推播支援 LINE retry key。若需即時可靠排程或多 worker，需另外設計持久佇列與排程服務。

API key、LINE token、Google OAuth JSON 不能提交 Git。服務不記錄訊息內文、簽章、token 或使用者 ID。不能用此機器人直接寄出 Email。

官方文件：[LINE webhook 驗證](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)、[LINE Messaging API](https://developers.line.biz/en/reference/messaging-api/)、[Render Web Services](https://render.com/docs/web-services)、[Render 免費方案](https://render.com/docs/free)。

## 邀約圖卡與標籤

`POST /api/invitations` 使用同一 Bearer API key，JSON 欄位 `invitations` 為陣列。每件需要 `thread_id`、`category`、`brand`、`summary`、`placement`、`schedule`、`authorization`。六個類別按固定順序呈現，摘要自動帶入品牌名稱。

三個按鈕為「幫我婉拒」「可以報價」「公關品可收」，透過簽名 postback 套用同名 Gmail 標籤。成功不回覆，只有失敗通知。不發送 Email，不移除既有標籤。

分類另需環境變數 `GMAIL_ACCOUNT_EMAIL` 和 `GMAIL_CREDENTIALS_JSON`，後者為專用 Gmail modify OAuth JSON，含 account_email、client_id、client_secret、refresh_token。不可放在 GitHub。

圖卡底色 #FFBDD9，標題與連結 #4D3440，欄位標題 #654455，內文 #222837；分類按鈕白底深色字。同類別的不同品牌各自一張卡，依類別排序後左右滑動。
