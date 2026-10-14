from unittest.mock import Mock, patch

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from ..graph import GraphClient, GraphClientError, TokenExpiredError, _GraphTransport


class GraphSDKTests(SimpleTestCase):
    @patch("crm.graph.requests.get")
    def test_sdk_operations_use_delegated_bearer_and_bounded_transport(self, get):
        response = Mock(status_code=200, headers={})
        get.return_value = response
        client = GraphClient("delegated-token")
        for endpoint in (
            "/me",
            "/me/photo/$value",
            "/me/calendarView",
            "/me/mailFolders/inbox/messages",
            "/me/presence",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIs(
                    client._request(endpoint, {"$skiptoken": "opaque+/= value"}),
                    response,
                )
                args, kwargs = get.call_args
                self.assertEqual(args, (client.GRAPH_BASE + endpoint,))
                self.assertEqual(kwargs["params"], {"$skiptoken": "opaque+/= value"})
                self.assertEqual(
                    kwargs["headers"]["Authorization"], "Bearer " + "delegated-token"
                )
                self.assertEqual(kwargs["timeout"], 10)
                self.assertIs(kwargs["allow_redirects"], False)

    @patch("crm.graph.requests.get")
    def test_unknown_operations_never_send_credentials(self, get):
        for endpoint in ("/users/other", "//evil.example/me", "/me/../users/other"):
            with self.subTest(endpoint=endpoint), self.assertRaises(GraphClientError):
                GraphClient("delegated-token")._request(endpoint)
        get.assert_not_called()

    @patch("crm.graph.requests.get")
    def test_transport_restricts_destination_and_method(self, get):
        for method, url in (
            ("GET", "https://evil.example/v1.0/me"),
            ("GET", "http://graph.microsoft.com/v1.0/me"),
            ("GET", "https://graph.microsoft.com/v1.0/users/other"),
            ("GET", "https://user@graph.microsoft.com/v1.0/me"),
            ("POST", "https://graph.microsoft.com/v1.0/me"),
        ):
            with self.subTest(url=url, method=method), self.assertRaises(GraphClientError):
                async_to_sync(_GraphTransport().handle_async_request)(
                    httpx.Request(method, url)
                )
        get.assert_not_called()

    @patch("crm.graph.requests.get")
    def test_missing_delegated_token_requires_authorization(self, get):
        with self.assertRaises(TokenExpiredError):
            GraphClient("").get_profile()
        get.assert_not_called()
