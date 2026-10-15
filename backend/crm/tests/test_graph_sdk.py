from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from ..graph import GraphClient, GraphClientError, TokenExpiredError


class GraphSDKTests(SimpleTestCase):
    @patch("httpx.AsyncHTTPTransport.handle_async_request")
    def test_sdk_operations_use_delegated_bearer_and_bounded_transport(self, send):
        client = GraphClient("delegated-token")
        start = datetime.now(timezone.utc)
        for operation, endpoint, data in (
            (client.get_profile, "/me", {"id": "user"}),
            (client.get_photo, "/me/photo/$value", None),
            (lambda: client.get_calendar_view(start, start + timedelta(days=1)), "/me/calendarView", {"value": []}),
            (client.get_mailbox_messages, "/me/mailFolders/inbox/messages", {"value": []}),
            (client.get_presence, "/me/presence", {}),
        ):
            with self.subTest(endpoint=endpoint):
                send.return_value = (
                    httpx.Response(200, content=b"photo")
                    if endpoint == "/me/photo/$value"
                    else httpx.Response(200, json=data)
                )
                operation()
                request = send.call_args.args[0]
                self.assertEqual(request.url.host, "graph.microsoft.com")
                self.assertEqual(request.url.path, "/v1.0" + endpoint)
                self.assertEqual(request.method, "GET")
                self.assertEqual(request.headers["Authorization"], "Bearer " + "delegated-token")
                self.assertTrue(all(value == 10 for value in request.extensions["timeout"].values()))

    @patch("httpx.AsyncHTTPTransport.handle_async_request")
    def test_redirects_and_throttling_do_not_send_additional_requests(self, send):
        for status in (302, 429, 503):
            with self.subTest(status=status):
                send.reset_mock()
                send.return_value = httpx.Response(
                    status,
                    headers={"Location": "https://evil.example/steal", "Retry-After": "1"},
                    json={"error": {"code": "unavailable", "message": "private"}},
                )
                with self.assertRaises(GraphClientError):
                    GraphClient("delegated-token").get_profile()
                send.assert_called_once()

    @patch("httpx.AsyncHTTPTransport.handle_async_request")
    def test_missing_delegated_token_requires_authorization(self, send):
        with self.assertRaises(TokenExpiredError):
            GraphClient("").get_profile()
        send.assert_not_called()
