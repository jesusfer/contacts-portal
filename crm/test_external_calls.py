from unittest.mock import Mock, patch

import requests
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount import app_settings
from django.core.cache import cache
from django.test import SimpleTestCase

from .external_calls import logged_request
from .graph import GraphClient, GraphClientError, TokenExpiredError
from .teams import _get_jwks, exchange_graph_token


class ExternalCallLoggingTests(SimpleTestCase):
    def response(self, status=200, data=None, headers=None):
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers or {})
        response._content = b'{"access_token": "response-secret", "email": "private@example.com"}'
        response.json = Mock(return_value=data)
        return response

    @patch("crm.external_calls.monotonic", side_effect=[1, 1.125])
    def test_success_has_matching_call_id_duration_and_safe_metadata(self, clock):
        request_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        correlation_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        response = self.response(headers={
            "x-ms-request-id": request_id,
            "x-ms-correlation-id": correlation_id,
            "Set-Cookie": "cookie-secret",
        })
        send = Mock(return_value=response)
        userinfo = "test-user:fake-password"
        url = f"https://{userinfo}@example.com/private-person?code=code-secret#fragment-secret"
        kwargs = {
            "headers": {"Authorization": "Bearer " + "access-secret"},
            "data": {"client_secret": "client-secret", "refresh_token": "refresh-secret"},
            "timeout": 10, "allow_redirects": False,
        }
        with self.assertLogs("crm.external_calls", level="INFO") as logs:
            result = logged_request(
                send, url, service="oidc", operation="token_exchange", method="POST", **kwargs,
            )
        self.assertIs(result, response)
        send.assert_called_once_with(url, **kwargs)
        self.assertEqual(len(logs.records), 2)
        start, complete = [record.getMessage() for record in logs.records]
        self.assertEqual(start.split("call_id=")[1].split()[0], complete.split("call_id=")[1].split()[0])
        self.assertIn("service=oidc operation=token_exchange method=POST host='example.com'", start)
        self.assertIn("status=200 duration_ms=125.0", complete)
        self.assertIn(f"request_id={request_id} correlation_id={correlation_id}", complete)
        self.assertNotIn("duration_ms=", start)
        self.assertNotIn("response-secret", complete)
        for secret in (
            "fake-password", "test-user", "private-person", "code-secret", "fragment-secret", "access-secret",
            "client-secret", "refresh-secret", "cookie-secret", "private@example.com",
        ):
            self.assertNotIn(secret, "\n".join(logs.output))

    def test_http_failures_are_warnings_and_untrusted_headers_are_omitted(self):
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status):
                response = self.response(status, headers={
                    "request-id": "untrusted-secret\nforged-log",
                    "client-request-id": "untrusted-secret",
                })
                with self.assertLogs("crm.external_calls", level="INFO") as logs:
                    self.assertIs(logged_request(
                        Mock(return_value=response), "https://graph.microsoft.com/v1.0/me",
                        service="graph", operation="/me", method="GET",
                    ), response)
                self.assertEqual(logs.records[-1].levelname, "WARNING")
                self.assertIn(f"status={status}", logs.output[-1])
                self.assertIn("request_id=- correlation_id=-", logs.output[-1])
                self.assertNotIn("untrusted-secret", "\n".join(logs.output))
                self.assertNotIn("forged-log", "\n".join(logs.output))

    def test_transport_errors_preserve_exception_without_logging_sensitive_message(self):
        for error in (requests.Timeout, requests.ConnectionError):
            with self.subTest(error=error):
                exception = error("https://example.com?token=exception-secret")
                with self.assertLogs("crm.external_calls", level="INFO") as logs:
                    with self.assertRaises(error) as raised:
                        logged_request(
                            Mock(side_effect=exception), "https://example.com",
                            service="oidc", operation="discovery", method="GET",
                        )
                self.assertIs(raised.exception, exception)
                self.assertIn("external_call.failed", logs.output[-1])
                self.assertIn(f"error_type={error.__name__}", logs.output[-1])
                self.assertEqual(logs.records[-1].levelname, "WARNING")
                self.assertIsNone(logs.records[-1].exc_info)
                self.assertNotIn("exception-secret", "\n".join(logs.output))

    def test_warning_level_suppresses_successful_calls(self):
        with self.assertNoLogs("crm.external_calls", level="WARNING"):
            logged_request(
                Mock(return_value=self.response()), "https://example.com",
                service="oidc", operation="discovery", method="GET",
            )

    @patch("crm.graph.requests.get")
    def test_graph_requests_and_photos_are_traced_without_changing_behavior(self, get):
        client = GraphClient("access-secret")
        for endpoint in (
            "/me", "/me/calendarView", "/me/mailFolders/inbox/messages", "/me/presence",
        ):
            with self.subTest(endpoint=endpoint):
                get.return_value = self.response()
                with self.assertLogs("crm.external_calls") as logs:
                    client._request(endpoint, {"$skiptoken": "cursor-secret"})
                self.assertIn(f"operation={endpoint}", logs.output[-1])
                self.assertNotIn("cursor-secret", "\n".join(logs.output))
                self.assertNotIn("access-secret", "\n".join(logs.output))
                self.assertEqual(get.call_args.kwargs["timeout"], 10)
                self.assertFalse(get.call_args.kwargs["allow_redirects"])
        for status, expected in ((200, bytes), (404, type(None))):
            with self.subTest(photo_status=status):
                get.return_value = self.response(status)
                with self.assertLogs("crm.external_calls") as logs:
                    self.assertIsInstance(client.get_photo(), expected)
                self.assertIn("operation=/me/photo/$value", logs.output[-1])
                self.assertIn(f"status={status}", logs.output[-1])
        get.return_value = self.response()
        with self.assertLogs("crm.external_calls") as logs:
            client._request("/users/private-person")
        self.assertIn("operation=http_request", logs.output[-1])
        self.assertNotIn("private-person", "\n".join(logs.output))

    @patch("crm.graph.requests.get")
    def test_graph_errors_keep_existing_public_exceptions(self, get):
        for status, error in ((401, TokenExpiredError), (403, TokenExpiredError), (500, GraphClientError)):
            with self.subTest(status=status), self.assertLogs("crm.external_calls") as logs:
                get.return_value = self.response(status)
                with self.assertRaises(error):
                    GraphClient("access-secret").get_presence()
                self.assertIn(f"status={status}", logs.output[-1])
        get.side_effect = requests.Timeout("exception-secret")
        with self.assertLogs("crm.external_calls") as logs, self.assertRaises(GraphClientError):
            GraphClient("access-secret").get_photo()
        self.assertNotIn("exception-secret", "\n".join(logs.output))

    @patch("crm.teams.requests.post")
    def test_entra_token_exchanges_are_traced_without_credentials(self, post):
        tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        app = {
            "client_id": "client-id", "secret": "client-secret",
            "settings": {"server_url": f"https://login.microsoftonline.com/{tenant}/v2.0"},
        }
        token = {"access_token": "response-secret", "token_type": "Bearer", "expires_in": 3600}
        post.return_value = self.response(data=token)
        for arguments, operation in (
            ({"assertion": "assertion-secret"}, "on_behalf_of"),
            ({"refresh_token": "refresh-secret"}, "refresh_token"),
        ):
            with self.subTest(operation=operation), self.assertLogs("crm.external_calls") as logs:
                self.assertIs(exchange_graph_token(app, **arguments), token)
                self.assertIn(f"service=entraid operation={operation} method=POST", logs.output[-1])
                for secret in ("assertion-secret", "refresh-secret", "client-secret", "response-secret", tenant):
                    self.assertNotIn(secret, "\n".join(logs.output))
                self.assertEqual(post.call_args.kwargs["timeout"], 10)
                self.assertFalse(post.call_args.kwargs["allow_redirects"])
                self.assertEqual(post.call_args.kwargs["data"]["client_secret"], "client-secret")

    @patch("crm.teams.requests.get")
    def test_signing_key_cache_only_traces_actual_requests(self, get):
        tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        key = f"teams_jwks_{tenant}"
        cache.delete(key)
        self.addCleanup(cache.delete, key)
        keys = {"keys": []}
        get.return_value = self.response(data=keys)
        with self.assertLogs("crm.external_calls") as logs:
            self.assertEqual(_get_jwks(tenant), keys)
        self.assertIn("service=entraid operation=signing_keys method=GET", logs.output[-1])
        with self.assertNoLogs("crm.external_calls"):
            self.assertEqual(_get_jwks(tenant), keys)
        self.assertEqual(get.call_count, 1)
        with self.assertLogs("crm.external_calls"):
            self.assertEqual(_get_jwks(tenant, reload=True), keys)
        self.assertEqual(get.call_count, 2)

    @patch("requests.sessions.Session.send")
    def test_allauth_session_traces_oidc_requests_and_preserves_timeout(self, send):
        send.return_value = self.response()
        endpoints = (
            ("/.well-known/openid-configuration", "discovery", "GET"),
            ("/oauth2/v2.0/token", "token_exchange", "POST"),
            ("/discovery/v2.0/keys", "signing_keys", "GET"),
            ("/userinfo", "userinfo", "GET"),
            ("/custom/private-person", "http_request", "GET"),
        )
        with get_adapter().get_requests_session() as session:
            for path, operation, method in endpoints:
                with self.subTest(path=path), self.assertLogs("crm.external_calls") as logs:
                    response = session.request(
                        method, f"https://example.com{path}?code=code-secret",
                        headers={"Authorization": "Bearer " + "access-secret"},
                        data={"client_secret": "client-secret"},
                    )
                    self.assertIs(response, send.return_value)
                    self.assertIn(f"service=oidc operation={operation} method={method}", logs.output[-1])
                    for secret in ("code-secret", "access-secret", "client-secret", "response-secret", "private-person"):
                        self.assertNotIn(secret, "\n".join(logs.output))
                    self.assertEqual(send.call_args.kwargs["timeout"], app_settings.REQUESTS_TIMEOUT)
            with self.assertLogs("crm.external_calls"):
                session.get("https://example.com/userinfo", timeout=3, allow_redirects=False)
            self.assertEqual(send.call_args.kwargs["timeout"], 3)
            self.assertFalse(send.call_args.kwargs["allow_redirects"])

    @patch("requests.sessions.Session.send", side_effect=requests.Timeout("exception-secret"))
    def test_allauth_transport_failure_is_traced_and_reraised(self, send):
        with get_adapter().get_requests_session() as session:
            with self.assertLogs("crm.external_calls") as logs, self.assertRaises(requests.Timeout):
                session.get("https://example.com/.well-known/openid-configuration")
        self.assertIn("external_call.failed", logs.output[-1])
        self.assertNotIn("exception-secret", "\n".join(logs.output))
