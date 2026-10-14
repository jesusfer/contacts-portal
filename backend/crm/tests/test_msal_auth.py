"""Exercise MSAL's real acquisition/cache behavior with a mocked HTTP boundary."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import msal
import requests
from django.test import SimpleTestCase, override_settings

from ..graph import GraphClientError, TokenExpiredError
from ..teams.auth import (
    GRAPH_SCOPES,
    GraphTokenManager,
    MSALHTTPClient,
    TeamsConfigError,
    exchange_graph_token,
)


TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
APP = {
    "client_id": "test-client",
    "secret": "test-client-secret",
    "settings": {
        "server_url": f"https://login.microsoftonline.com/{TENANT}/v2.0",
    },
}
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    return result


class MSALAcquisitionTests(SimpleTestCase):
    def setUp(self):
        self.get = patch("crm.teams.auth.requests.get").start()
        self.post = patch("crm.teams.auth.requests.post").start()
        self.addCleanup(patch.stopall)
        self.get.return_value = response(
            {
                "authorization_endpoint": AUTHORITY + "/oauth2/v2.0/authorize",
                "token_endpoint": AUTHORITY + "/oauth2/v2.0/token",
                "issuer": AUTHORITY + "/v2.0",
            }
        )
        self.result = {
            "access_token": "acquired-access-secret",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "cached-refresh-secret",
            "client_info": base64.urlsafe_b64encode(
                json.dumps({"uid": "object-id", "utid": TENANT}).encode()
            ).decode().rstrip("="),
        }
        self.post.return_value = response(self.result)

    def test_obo_uses_msal_decorated_scopes_and_serializes_stripped_refresh_token(self):
        cache = msal.SerializableTokenCache()
        with self.assertLogs("crm.external_calls") as logs:
            result = exchange_graph_token(APP, assertion="assertion-secret", token_cache=cache)
        self.assertEqual(result["access_token"], self.result["access_token"])
        data = self.post.call_args.kwargs["data"]
        self.assertEqual(data["requested_token_use"], "on_behalf_of")
        self.assertEqual(data["assertion"], "assertion-secret")
        self.assertEqual(data["grant_type"], "urn:ietf:params:oauth:grant-type:jwt-bearer")
        self.assertIn("offline_access", data["scope"].split())
        self.assertIn(GRAPH_SCOPES[0], data["scope"].split())
        serialized = cache.serialize()
        self.assertIn("cached-refresh-secret", serialized)
        self.assertNotIn("assertion-secret", serialized)
        for secret in ("assertion-secret", "cached-refresh-secret", "acquired-access-secret"):
            self.assertNotIn(secret, "\n".join(logs.output))
        for send in (self.get, self.post):
            self.assertEqual(send.call_args.kwargs["timeout"], 10)
            self.assertFalse(send.call_args.kwargs["allow_redirects"])

    def test_imported_oidc_refresh_token_rotates_then_refreshes_silently_from_cache(self):
        cache = msal.SerializableTokenCache()
        result = exchange_graph_token(
            APP, refresh_token="oidc-refresh-secret", token_cache=cache
        )
        self.assertEqual(
            self.post.call_args.kwargs["data"]["refresh_token"], "oidc-refresh-secret"
        )
        restored = msal.SerializableTokenCache()
        restored.deserialize(cache.serialize())
        self.result["access_token"] = "rotated-access-secret"
        self.result["refresh_token"] = "rotated-refresh-secret"
        self.post.return_value = response(self.result)
        result = exchange_graph_token(APP, token_cache=restored)
        self.assertEqual(result["access_token"], "rotated-access-secret")
        self.assertEqual(
            self.post.call_args.kwargs["data"]["refresh_token"], "cached-refresh-secret"
        )
        credentials = list(restored.search(msal.TokenCache.CredentialType.REFRESH_TOKEN))
        self.assertEqual([item["secret"] for item in credentials], ["rotated-refresh-secret"])

    def test_obo_without_client_info_can_refresh_from_its_isolated_cache(self):
        self.result.pop("client_info")
        self.post.return_value = response(self.result)
        cache = msal.SerializableTokenCache()
        exchange_graph_token(APP, assertion="assertion-secret", token_cache=cache)
        self.result.pop("refresh_token")
        self.post.return_value = response(self.result)
        exchange_graph_token(APP, token_cache=cache)
        self.assertEqual(
            self.post.call_args.kwargs["data"]["refresh_token"], "cached-refresh-secret"
        )
        self.assertIn("cached-refresh-secret", cache.serialize())

    @patch("crm.teams.auth.msal.ConfidentialClientApplication")
    def test_acquisition_errors_and_malformed_results_have_stable_errors(self, application):
        for result, expected in (
            ({"error": "invalid_grant", "error_description": "private-secret"}, TokenExpiredError),
            ({"error": "interaction_required"}, TokenExpiredError),
            ({"error": "server_error"}, GraphClientError),
            ({"error": "invalid_client"}, GraphClientError),
            (None, GraphClientError),
            ([], GraphClientError),
            ({}, GraphClientError),
            ({"access_token": "secret"}, GraphClientError),
        ):
            with self.subTest(result=result):
                application.return_value.acquire_token_on_behalf_of.return_value = result
                with self.assertRaises(expected) as raised:
                    exchange_graph_token(APP, assertion="assertion-secret")
                self.assertNotIn("private-secret", str(raised.exception))
        for exception in (
            requests.Timeout("private-secret"),
            requests.ConnectionError("private-secret"),
            ValueError("private-secret"),
        ):
            with self.subTest(exception=type(exception)):
                application.return_value.acquire_token_on_behalf_of.side_effect = exception
                with self.assertRaises(GraphClientError):
                    exchange_graph_token(APP, assertion="assertion-secret")

    def test_transport_service_redirect_discovery_and_malformed_failures(self):
        for status in (302, 429, 500):
            with self.subTest(status=status):
                self.post.return_value = response({}, status)
                with self.assertRaises(GraphClientError):
                    exchange_graph_token(APP, assertion="assertion-secret")
        self.post.return_value = response({"error": "invalid_grant"}, 400)
        with self.assertRaises(TokenExpiredError):
            exchange_graph_token(APP, assertion="assertion-secret")
        self.post.return_value = response({})
        self.post.return_value._content = b"not-json"
        with self.assertLogs("crm.external_calls") as logs:
            with self.assertRaises(GraphClientError):
                exchange_graph_token(APP, assertion="assertion-secret")
        self.assertNotIn("not-json", "\n".join(logs.output))
        self.get.return_value = response({}, 302)
        with self.assertRaises(GraphClientError):
            exchange_graph_token(APP, assertion="assertion-secret")

    def test_empty_cache_cannot_use_another_requests_credentials(self):
        first_cache = msal.SerializableTokenCache()
        exchange_graph_token(APP, assertion="assertion-secret", token_cache=first_cache)
        self.post.reset_mock()
        with self.assertRaises(TokenExpiredError):
            exchange_graph_token(APP, token_cache=msal.SerializableTokenCache())
        self.post.assert_not_called()

    def test_transport_overrides_unbounded_or_redirecting_msal_options(self):
        transport = MSALHTTPClient("on_behalf_of")
        transport.post(AUTHORITY + "/token", timeout=None, allow_redirects=True)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 10)
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])

    @override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
    def test_credentials_are_never_written_to_client_side_sessions(self):
        request = SimpleNamespace(session={}, user=SimpleNamespace(pk=1))
        with self.assertRaises(TeamsConfigError):
            GraphTokenManager.store_token(
                request, "provider", "access-secret", refresh_token="refresh-secret"
            )
        self.assertEqual(request.session, {})
