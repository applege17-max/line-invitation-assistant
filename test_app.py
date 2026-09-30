import base64
import hashlib
import hmac
import io
import json
import os
import unittest
from unittest.mock import patch
import app


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "LINE_CHANNEL_SECRET": "test-secret", "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
            "LINE_USER_ID": "Uowner", "NOTIFY_API_KEY": "private-test-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.application = app.NotificationApp()

    def call(self, path, data=None, **headers):
        body = json.dumps(data).encode() if data is not None else b""
        env = {"PATH_INFO": path, "REQUEST_METHOD": "POST" if data is not None else "GET",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), **headers}
        status = []
        output = b"".join(self.application(env, lambda s, h: status.append(s)))
        return status[0], json.loads(output)

    def signed(self, data):
        return base64.b64encode(hmac.new(b"test-secret", json.dumps(data).encode(), hashlib.sha256).digest()).decode()

    @patch("app.line_call", return_value="test-request")
    def test_authentication_is_required_and_recipient_is_fixed(self, send):
        self.assertEqual(self.call("/api/notify", {"text": "hi"})[0], "401 Unauthorized")
        self.assertEqual(self.call("/api/notify", {"text": "hi", "to": "Ustranger"},
                                   HTTP_AUTHORIZATION="Bearer private-test-key")[0], "200 OK")
        self.assertEqual(send.call_args.args[1]["to"], "Uowner")
        self.assertEqual(send.call_count, 1)

    @patch("app.line_call", return_value="test-request")
    def test_signature_and_stranger_rejection(self, send):
        data = {"events": [{"type": "message", "replyToken": "r", "source": {"type": "user", "userId": "Ustranger"},
                            "message": {"type": "text", "text": "ping"}}]}
        self.assertEqual(self.call("/webhook", data)[0], "401 Unauthorized")
        self.assertEqual(self.call("/webhook", data, HTTP_X_LINE_SIGNATURE=self.signed(data))[0], "200 OK")
        send.assert_not_called()

    @patch("app.line_call", return_value="test-request")
    def test_owner_ping_and_redelivery(self, send):
        data = {"events": [{"webhookEventId": "event1", "type": "message", "replyToken": "r",
                            "source": {"type": "user", "userId": "Uowner"}, "message": {"type": "text", "text": "ping"}}]}
        for _ in range(2):
            self.assertEqual(self.call("/webhook", data, HTTP_X_LINE_SIGNATURE=self.signed(data))[0], "200 OK")
        self.assertEqual(send.call_count, 1)
        self.assertIn("連線正常", send.call_args.args[1]["messages"][0]["text"])

    def test_empty_verify_event_and_health(self):
        data = {"events": []}
        self.assertEqual(self.call("/webhook", data, HTTP_X_LINE_SIGNATURE=self.signed(data))[0], "200 OK")
        self.assertEqual(self.call("/healthz")[0], "200 OK")
        with patch.dict(os.environ, {"NOTIFY_API_KEY": ""}):
            self.assertEqual(self.call("/healthz")[0], "503 Service Unavailable")

    @patch("app.line_call", side_effect=RuntimeError("upstream"))
    def test_failed_push_does_not_report_success(self, send):
        self.assertEqual(self.call("/api/notify", {"text": "hi"},
                                   HTTP_AUTHORIZATION="Bearer private-test-key")[0], "502 Bad Gateway")

    def test_emoji_chunking_and_invalid_messages(self):
        messages = app.text_messages("😀" * 5000)
        self.assertEqual("".join(m["text"] for m in messages), "😀" * 5000)
        self.assertTrue(all(len(m["text"].encode("utf-16-le")) // 2 <= 4500 for m in messages))
        for invalid in (" ", None, "a" * 22501):
            with self.assertRaises(ValueError):
                app.text_messages(invalid)

    @patch("app.line_call", return_value="test-request")
    @patch("app.apply_label")
    def test_successful_postback_is_silent(self, label, send):
        from invitations import action_data
        data = {"events": [{"type": "postback", "replyToken": "r", "source": {"type": "user", "userId": "Uowner"},
                            "postback": {"data": action_data("1a0e8e45ba3e76d2", "quote")}}]}
        self.assertEqual(self.call("/webhook", data, HTTP_X_LINE_SIGNATURE=self.signed(data))[0], "200 OK")
        label.assert_called_once_with("1a0e8e45ba3e76d2", "可以報價")
        send.assert_not_called()

    @patch("app.line_call", return_value="test-request")
    @patch("app.apply_label", side_effect=RuntimeError("Gmail 標籤權限尚未完成連接"))
    def test_failed_postback_notifies_owner(self, label, send):
        from invitations import action_data
        data = {"events": [{"type": "postback", "replyToken": "r", "source": {"type": "user", "userId": "Uowner"},
                            "postback": {"data": action_data("1a0e8e45ba3e76d2", "gift")}}]}
        self.assertEqual(self.call("/webhook", data, HTTP_X_LINE_SIGNATURE=self.signed(data))[0], "200 OK")
        self.assertIn("分類失敗", send.call_args.args[1]["messages"][0]["text"])

    def test_cards_order_buttons_brand_and_action_tampering(self):
        from invitations import cards, verify_action
        item = {"thread_id": "1a0e8e45ba3e76d2", "brand": "測試品牌", "summary": "產品", "placement": "IG",
                "schedule": "未提供", "authorization": "未提供", "category": "團購"}
        result = cards([item, {**item, "category": "付費影音"}])
        bubble = result[0]["contents"]["contents"][0]
        self.assertEqual(bubble["header"]["contents"][0]["text"], "付費影音")
        self.assertEqual(bubble["body"]["backgroundColor"], "#FFBDD9")
        self.assertEqual([b["action"]["label"] for b in bubble["footer"]["contents"][:3]],
                         ["幫我婉拒", "可以報價", "公關品可收"])
        data = bubble["footer"]["contents"][0]["action"]["data"]
        self.assertEqual(verify_action(data)[1], "幫我婉拒")
        with self.assertRaises(ValueError):
            verify_action(data.replace("decline", "quote"))

    @patch("app.line_call", return_value="test-request")
    @patch("app.unread_cards", return_value=[{"type": "text", "text": "未讀邀約"}])
    def test_owner_can_query_unread_invitations(self, unread, send):
        data = {"events": [{"webhookEventId": "query1", "type": "message", "replyToken": "r",
                            "source": {"type": "user", "userId": "Uowner"}, "message": {"type": "text", "text": "業配"}}]}
        self.assertEqual(self.call("/webhook", data, HTTP_X_LINE_SIGNATURE=self.signed(data))[0], "200 OK")
        unread.assert_called_once()
        self.assertEqual(send.call_args.args[1]["messages"][0]["text"], "未讀邀約")

    def test_two_brands_in_same_category_make_two_cards(self):
        from invitations import cards
        item = {"thread_id": "1a0e8e45ba3e76d2", "brand": "品牌 A", "summary": "產品 A", "placement": "IG",
                "schedule": "未提供", "authorization": "未提供", "category": "付費影音"}
        bubbles = cards([item, {**item, "brand": "品牌 B", "summary": "產品 B", "thread_id": "1a0cc290cb4378bc"}])[0]["contents"]["contents"]
        self.assertEqual(len(bubbles), 2)
        self.assertEqual([b["body"]["contents"][0]["text"] for b in bubbles], ["品牌 A", "品牌 B"])


class NoonWindowTests(unittest.TestCase):
    @patch("invitations.gmail_client")
    @patch("invitations.time.time", return_value=1790740800)
    def test_taipei_noon_window_uses_epoch_not_server_timezone(self, clock, client):
        # 2026-09-30 12:00:00 in Asia/Taipei.
        import invitations
        client.return_value.return_value = {"messages": []}
        result = invitations.unread_cards()
        from urllib.parse import parse_qs
        query = parse_qs(client.return_value.call_args.args[0].split("?", 1)[1])["q"][0]
        self.assertIn("after:1790740799", query)
        self.assertIn("before:1790740801", query)
        self.assertIn("is:unread", query)
        self.assertIn("12:00", result[0]["text"])

    @patch("invitations.gmail_client")
    @patch("invitations.time.time", return_value=1790740799)
    def test_before_noon_does_not_search_yesterday(self, clock, client):
        import invitations
        result = invitations.unread_cards()
        client.assert_not_called()
        self.assertIn("尚未", result[0]["text"])


if __name__ == "__main__":
    unittest.main()
