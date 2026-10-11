import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from allauth.account.signals import user_logged_in as allauth_user_logged_in
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.internal.flows.login import complete_login
from allauth.socialaccount.models import SocialAccount, SocialToken
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import signing
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import path
from django.utils import timezone

from config.settings import configure_oidc
from config.urls import urlpatterns as project_patterns
from .graph import GraphClient, GraphClientError, InvalidCursor, TokenExpiredError
from .signals import AUTH_SOURCE_SESSION_KEY
from .models import UserProfile
from .openid_connect import TenantAwareOpenIDConnectOAuth2Adapter
from .teams import GraphTokenManager, TeamsTokenError, validate_teams_token


class TeamsGraphFrontendTests(SimpleTestCase):
    def test_sdk_identity_binding_and_teams_auth_endpoint_selection(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for frontend validation.")
        source = Path(__file__).resolve().parent.parent / "frontend/src"
        result = subprocess.run(
            [node, "--input-type=module", "-e", f"""
import assert from 'node:assert/strict'
import {{ readFileSync }} from 'node:fs'
globalThis.window = {{ location: {{ origin: 'https://portal.example.com' }} }}
globalThis.document = {{ cookie: 'csrftoken=csrf-test' }}
let account = {{ tenantId: 'tenant' }}
let graphId = 'object'
class App {{
  constructor() {{
    this.msalInstance = {{
      acquireTokenSilent: async () => ({{ account }}),
      setActiveAccount: value => {{ assert.equal(value, account) }},
    }}
    this.graph = {{ call: async () => ({{ id: graphId }}) }}
  }}
  async start() {{}}
}}
globalThis.sdkMock = {{ App }}
globalThis.meMock = {{ get: {{}} }}
const code = readFileSync({json.dumps(str(source / 'teams-graph.js'))}, 'utf8')
  .replace("import('@microsoft/teams.client')", 'Promise.resolve(globalThis.sdkMock)')
  .replace("import('@microsoft/teams.graph-endpoints/me')", 'Promise.resolve(globalThis.meMock)')
const graph = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))
const provider = {{ client_id: 'client', tenant_id: 'tenant' }}
await graph.startTeamsGraph(provider, 'object')
assert.equal(graph.teamsGraphActive(), true)
account = {{ tenantId: 'other-tenant' }}
await assert.rejects(graph.startTeamsGraph(provider, 'object'), /tenant does not match/)
assert.equal(graph.teamsGraphActive(), false)
account = {{ tenantId: 'tenant' }}
graphId = 'other-object'
await assert.rejects(graph.startTeamsGraph(provider, 'object'), /identity does not match/)
assert.equal(graph.teamsGraphActive(), false)
graphId = 'object'
const controller = new AbortController()
controller.abort()
await assert.rejects(graph.startTeamsGraph(provider, 'object', controller.signal))
assert.equal(graph.teamsGraphActive(), false)
const teams = await import({json.dumps((source / 'teams.js').as_uri())})
let requests = []
globalThis.fetch = async (url, options) => {{
  requests.push({{ url, options }})
  return {{ ok: true, json: async () => ({{ id: 'object', tenant_id: 'tenant' }}) }}
}}
await teams.exchangeTeamsToken('entraid-work', 'portal-token', {{ graphOnly: true }})
await teams.exchangeTeamsToken('entraid-work', 'portal-token')
assert.deepEqual(requests.map(r => r.url), ['/api/teams/identity/', '/api/teams/sso/'])
for (const {{ options }} of requests) {{
  assert.equal(options.headers['X-CSRFToken'], 'csrf-test')
  assert.deepEqual(JSON.parse(options.body), {{ provider_id: 'entraid-work', token: 'portal-token' }})
}}
"""],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sdk_pagination_restricts_destinations_and_preserves_query(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for frontend validation.")
        module = (Path(__file__).resolve().parent.parent / "frontend/src/teams-graph.js").as_uri()
        result = subprocess.run(
            [node, "--input-type=module", "-e", f"""
import assert from 'node:assert/strict'
import {{ graphPageQuery, clearTeamsGraph, teamsGraphActive, teamsGraphRequest }} from {json.dumps(module)}
const endpoint = '/me/calendarView'
const params = {{ startDateTime: '2026-10-01T00:00:00Z', $top: '50' }}
const link = 'https://graph.microsoft.com/v1.0/me/calendarView?$skiptoken=next'
assert.deepEqual(graphPageQuery(link, endpoint, params), {{ ...params, $skiptoken: 'next' }})
for (const invalid of [
  link.replace('graph.microsoft.com', 'evil.example'),
  link.replace('https:', 'http:'),
  link.replace('/me/calendarView', '/users/other/calendarView'),
  link + '&startDateTime=other',
  link + '&$skiptoken=duplicate',
  link + '&unexpected=value',
  link + '#fragment',
  link.replace('graph.microsoft.com', 'user@graph.microsoft.com'),
]) assert.throws(() => graphPageQuery(invalid, endpoint, params))
clearTeamsGraph()
assert.equal(teamsGraphActive(), false)
await assert.rejects(teamsGraphRequest('graph/inbox/'), /not initialized/)
"""],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


TENANT = "22222222-2222-2222-2222-222222222222"
CLIENT = "11111111-1111-1111-1111-111111111111"
OID = "33333333-3333-3333-3333-333333333333"
AZP = "1fec8e78-bce4-4aaf-ab1b-5451cc387264"
PROVIDERS = configure_oidc(json.dumps([{
    "id": "entraid-work", "name": "Work identity",
    "server_url": f"https://login.microsoftonline.com/{TENANT}/v2.0",
    "client_id": CLIENT, "client_secret": "fixture",
    "teams": {"resource": f"api://portal.example.com/{CLIENT}"},
}, {
    "id": "entraid-other", "name": "Other",
    "server_url": "https://identity.example.com",
    "client_id": "other", "client_secret": "fixture",
}]), debug=False)
APP = PROVIDERS["openid_connect"]["APPS"][0]
CONFIG = APP["settings"]["teams"]
PROFILE = {
    "id": OID, "displayName": "Graph Name", "givenName": "Graph",
    "surname": "Name", "mail": "graph@example.com", "userPrincipalName": "upn@example.com",
}


def response(data=None, status=200, content=b""):
    return Mock(status_code=status, content=content, json=Mock(return_value=data))


def verified_callback(request, provider_id, process):
    provider = get_adapter().get_provider(request, provider_id)
    sociallogin = provider.sociallogin_from_response(request, {
        "id_token": {"sub": "oidc-sub", "oid": OID, "tid": TENANT},
        "userinfo": {"sub": "oidc-sub", "email": "unrelated@example.com"},
    })
    sociallogin.token = SocialToken(
        token="normal-access", token_secret="normal-refresh",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    sociallogin.state = {"process": process}
    return complete_login(request, sociallogin)


urlpatterns = [
    path("test/oidc/<provider_id>/<process>/", verified_callback),
    *project_patterns,
]


class SignedTokenMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = jwt.algorithms.RSAAlgorithm.to_jwk(cls.private_key.public_key(), as_dict=True)
        cls.jwks = {"keys": [{**key, "kid": "test-key", "alg": "RS256", "use": "sig"}]}

    def claims(self):
        now = int(timezone.now().timestamp())
        return {
            "exp": now + 3600, "nbf": now - 1, "iat": now - 1,
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": CLIENT, "tid": TENANT, "oid": OID, "sub": "teams-access-sub",
            "scp": "access_as_user", "azp": AZP,
        }

    def token(self, claims=None, key=None):
        return jwt.encode(
            claims or self.claims(), key or self.private_key,
            algorithm="RS256", headers={"kid": "test-key"},
        )

    def validate(self, claims):
        with patch("crm.teams._get_jwks", return_value=self.jwks):
            return validate_teams_token(self.token(claims), CONFIG)


class TeamsJWTTests(SignedTokenMixin, SimpleTestCase):
    def test_valid_signed_access_token(self):
        self.assertEqual(self.validate(self.claims())["oid"], OID)

    def test_each_required_claim_is_required(self):
        for key in self.claims():
            with self.subTest(claim=key):
                claims = self.claims()
                del claims[key]
                with self.assertRaises(TeamsTokenError):
                    self.validate(claims)

    def test_invalid_signature(self):
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with patch("crm.teams._get_jwks", return_value=self.jwks), self.assertRaises(TeamsTokenError):
            validate_teams_token(self.token(key=wrong_key), CONFIG)

    def test_algorithm_confusion_rejected(self):
        token = jwt.encode(self.claims(), "a-long-test-signing-key-not-rsa-123", algorithm="HS256",
                           headers={"kid": "test-key"})
        with patch("crm.teams._get_jwks") as keys, self.assertRaises(TeamsTokenError):
            validate_teams_token(token, CONFIG)
        keys.assert_not_called()

    def test_bad_audience_issuer_tenant_client_and_scope(self):
        for key, value in (
            ("aud", "another-client"), ("aud", [CLIENT, "another-client"]),
            ("iss", "https://identity.example.com"), ("tid", "other-tenant"),
            ("azp", "unknown-client"), ("azp", ""), ("scp", "not_access_as_user"),
            ("scp", "access_as_user_extra"), ("scp", ["access_as_user"]),
            ("oid", ""), ("sub", ""), ("oid", 123),
        ):
            with self.subTest(claim=key, value=value), self.assertRaises(TeamsTokenError):
                self.validate({**self.claims(), key: value})

    def test_timestamp_expiry_and_future_claims(self):
        now = int(timezone.now().timestamp())
        for key, value in (
            ("exp", now - 60), ("nbf", now + 60), ("iat", now + 60),
            ("exp", str(now + 3600)), ("nbf", True), ("iat", None),
        ):
            with self.subTest(claim=key, value=value), self.assertRaises(TeamsTokenError):
                self.validate({**self.claims(), key: value})

    def test_exact_scope_among_other_scopes(self):
        self.validate({**self.claims(), "scp": "other access_as_user another"})

    def test_unknown_key_refreshes_once(self):
        with patch("crm.teams._get_jwks", side_effect=[{"keys": []}, self.jwks]) as keys:
            validate_teams_token(self.token(), CONFIG)
        self.assertEqual(keys.call_count, 2)

    def test_organizations_issuer_uses_token_tenant_for_validation(self):
        other_tenant = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        claims = {
            **self.claims(), "tid": other_tenant,
            "iss": f"https://login.microsoftonline.com/{other_tenant}/v2.0",
        }
        with patch("crm.teams._get_jwks", return_value=self.jwks) as keys:
            result = validate_teams_token(self.token(claims), {**CONFIG, "tenant_id": "organizations"})
        self.assertEqual(result["tid"], other_tenant)
        keys.assert_called_once_with(other_tenant, reload=False)

    def test_organizations_issuer_rejects_non_uuid_tenant(self):
        with self.assertRaises(TeamsTokenError):
            self.validate({**self.claims(), "tid": "organizations"})

    @patch("crm.teams.requests.get")
    def test_jwks_fixed_tenant_endpoint_does_not_follow_redirect(self, get):
        cache.clear()
        get.return_value = response(status=302)
        with self.assertRaises(TeamsTokenError):
            validate_teams_token(self.token(), CONFIG)
        self.assertEqual(get.call_args.args[0],
                         f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys")
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)


class TenantAwareOIDCTests(SimpleTestCase):
    def token(self, tenant_id):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return jwt.encode({"tid": tenant_id}, key, algorithm="RS256", headers={"kid": "test-key"})

    @patch("crm.openid_connect.jwtkit.verify_and_decode")
    def test_organizations_oidc_issuer_is_bound_to_token_tenant(self, verify):
        token = self.token(TENANT)
        verify.return_value = {"tid": TENANT}
        adapter = Mock(
            get_provider=Mock(return_value=SimpleNamespace(
                app=SimpleNamespace(settings={"graph_tenant_id": "organizations"}),
            )),
            openid_config={"jwks_uri": "https://login.microsoftonline.com/organizations/keys"},
            did_fetch_access_token=True,
        )
        result = TenantAwareOpenIDConnectOAuth2Adapter._decode_id_token(adapter, SimpleNamespace(
            client_id=CLIENT,
        ), token)
        self.assertEqual(result, {"tid": TENANT})
        self.assertEqual(verify.call_args.kwargs["issuer"],
                         f"https://login.microsoftonline.com/{TENANT}/v2.0")
        self.assertEqual(verify.call_args.kwargs["keys_url"],
                         "https://login.microsoftonline.com/organizations/keys")

    @patch("crm.openid_connect.jwtkit.verify_and_decode")
    def test_organizations_oidc_rejects_tenant_identity_mismatch(self, verify):
        token = self.token(TENANT)
        verify.return_value = {"tid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}
        adapter = Mock(
            get_provider=Mock(return_value=SimpleNamespace(
                app=SimpleNamespace(settings={"graph_tenant_id": "organizations"}),
            )),
            openid_config={"jwks_uri": "https://login.microsoftonline.com/organizations/keys"},
            did_fetch_access_token=True,
        )
        with self.assertRaises(OAuth2Error):
            TenantAwareOpenIDConnectOAuth2Adapter._decode_id_token(adapter, SimpleNamespace(
                client_id=CLIENT,
            ), token)


class GraphClientTests(SimpleTestCase):
    def setUp(self):
        self.graph = GraphClient("graph-access", tenant_id=TENANT)

    @patch("crm.graph.requests.get")
    def test_profile_authorization_and_fixed_endpoint(self, get):
        get.return_value = response(PROFILE)
        self.assertEqual(self.graph.get_profile(), PROFILE)
        self.assertEqual(get.call_args.args[0], "https://graph.microsoft.com/v1.0/me")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer " + "graph-access")
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)
        self.assertIn("givenName", get.call_args.kwargs["params"]["$select"])

    @patch("crm.graph.requests.get")
    def test_errors_and_redirects_are_sanitized(self, get):
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status):
                get.return_value = response({"error": {"message": "graph-access"}}, status=status)
                with self.assertRaises(GraphClientError) as caught:
                    self.graph.get_profile()
                self.assertNotIn("graph-access", str(caught.exception))
                self.assertEqual(caught.exception.status, 403 if status in (401, 403) else 502)
        get.return_value = response([])
        with self.assertRaises(GraphClientError):
            self.graph.get_profile()

    @patch("crm.graph.requests.get")
    def test_photo_bytes_missing_and_redirect(self, get):
        get.return_value = response(content=b"\xff\xd8\x00\xff")
        self.assertEqual(self.graph.get_photo(), b"\xff\xd8\x00\xff")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer " + "graph-access")
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)
        get.return_value = response(status=404)
        self.assertIsNone(self.graph.get_photo())
        get.return_value = response(status=302)
        with self.assertRaises(GraphClientError):
            self.graph.get_photo()

    @patch("crm.graph.requests.get")
    def test_inbox_schema_body_and_signed_pagination(self, get):
        get.return_value = response({
            "value": [{
                "id": "message", "subject": "Hello",
                "body": {"contentType": "text", "content": "Full message"},
                "from": {"emailAddress": {"name": "Sender", "address": "sender@example.com"}},
                "receivedDateTime": "2026-10-10T12:00:00Z", "isRead": True,
            }],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
        })
        result = self.graph.get_mailbox_messages()
        self.assertEqual(result["emails"][0]["contact"], {"name": "Sender", "email": "sender@example.com"})
        self.assertEqual(result["emails"][0]["body"], "Full message")
        self.assertNotIn("https://", result["next_page"])
        self.assertNotIn("access", result["next_page"])
        self.assertIn('outlook.body-content-type="text"', get.call_args.kwargs["headers"]["Prefer"])
        page = result["next_page"]
        get.return_value = response({"value": []})
        self.assertEqual(self.graph.get_mailbox_messages(page=page), {"emails": [], "next_page": None})
        self.assertEqual(get.call_args.kwargs["params"]["$skip"], "25")
        self.assertIn("body,", get.call_args.kwargs["params"]["$select"])

    @patch("crm.graph.requests.get")
    def test_cursor_tamper_expiry_and_cross_identity(self, get):
        get.return_value = response({
            "value": [], "@odata.nextLink":
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=opaque",
        })
        page = self.graph.get_mailbox_messages()["next_page"]
        get.reset_mock()
        for bad in ("", "https://evil.example.com", page + "changed"):
            with self.subTest(page=bad), self.assertRaises(InvalidCursor):
                self.graph.get_mailbox_messages(page=bad)
        get.assert_not_called()
        for graph in (
            GraphClient("x", TENANT, user_id=8, provider_id="entraid-work"),
            GraphClient("x", TENANT, user_id=7, provider_id="other"),
            GraphClient("x", "other-tenant", user_id=7, provider_id="entraid-work"),
        ):
            with self.assertRaises(InvalidCursor):
                graph.get_mailbox_messages(page=page)
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 3601):
            with self.assertRaises(InvalidCursor):
                self.graph.get_mailbox_messages(page=page)
        with self.assertRaises(InvalidCursor):
            self.graph.get_calendar_view(timezone.now(), timezone.now() + timedelta(days=1), page=page)

    @patch("crm.graph.requests.get")
    def test_even_signed_cursors_require_the_exact_schema(self, get):
        for payload in (
            [], {"context": self.graph._context("inbox")},
            {"context": self.graph._context("inbox"), "params": {"$skip": 25}},
            {"context": self.graph._context("inbox"), "params": {"url": "https://evil.example.com"}},
            {"context": self.graph._context("inbox"), "params": {"$skip": "25", "$filter": "other"}},
            {"context": self.graph._context("inbox"), "params": {"$skiptoken": "x" * 8193}},
        ):
            with self.subTest(payload=payload):
                cursor = signing.dumps(payload, salt=self.graph.CURSOR_SALT)
                with self.assertRaises(InvalidCursor):
                    self.graph.get_mailbox_messages(page=cursor)
        get.assert_not_called()

    @patch("crm.graph.requests.get")
    def test_inbox_html_fallback_becomes_text_and_malformed_body_fails_safely(self, get):
        get.return_value = response({"value": [{
            "id": "mail", "body": {"contentType": "html", "content": "<p>Message</p>"},
        }]})
        self.assertEqual(self.graph.get_mailbox_messages()["emails"][0]["body"], "Message")
        get.return_value = response({"value": [{"body": "not-an-object"}]})
        with self.assertRaises(GraphClientError):
            self.graph.get_mailbox_messages()

    @patch("crm.graph.requests.get")
    def test_pagination_links_cannot_redirect_to_arbitrary_urls_or_resources(self, get):
        for link in (
            "https://evil.example.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "https://graph.microsoft.com@evil.example.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "http://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/users/other/messages?$skip=25",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25&redirect=evil",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25&$skip=50",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25#fragment",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25&$top=1000",
        ):
            with self.subTest(link=link):
                get.return_value = response({"value": [], "@odata.nextLink": link})
                with self.assertRaises(GraphClientError):
                    self.graph.get_mailbox_messages()
                self.assertEqual(get.call_args.args[0],
                                 "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages")

    @patch("crm.graph.requests.get")
    def test_calendar_utc_times_and_pagination(self, get):
        start = timezone.datetime(2026, 10, 10, 12, tzinfo=timezone.get_fixed_timezone(120))
        end = start + timedelta(days=1)
        get.return_value = response({
            "value": [{
                "id": "event", "subject": "Meeting",
                "start": {"dateTime": "2026-10-10T10:00:00.0000000", "timeZone": "UTC"},
                "end": {"dateTime": "2026-10-10T11:00:00.0000000", "timeZone": "UTC"},
            }],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?$skip=50",
        })
        result = self.graph.get_calendar_view(start, end)
        self.assertEqual(result["events"][0]["starts_at"], "2026-10-10T10:00:00+00:00")
        self.assertEqual(get.call_args.kwargs["params"]["startDateTime"], "2026-10-10T10:00:00+00:00")
        self.assertIn('outlook.timezone="UTC"', get.call_args.kwargs["headers"]["Prefer"])
        page = result["next_page"]
        get.return_value = response({"value": []})
        self.graph.get_calendar_view(timezone.now(), timezone.now() + timedelta(days=1), page=page)
        self.assertEqual(get.call_args.kwargs["params"]["$skip"], "50")
        self.assertEqual(get.call_args.kwargs["params"]["startDateTime"], "2026-10-10T10:00:00+00:00")


@override_settings(
    ROOT_URLCONF=__name__, SOCIALACCOUNT_PROVIDERS=PROVIDERS, SECURE_SSL_REDIRECT=False,
    ACCOUNT_AUTHENTICATED_LOGIN_REDIRECTS=False,
)
class GraphIntegrationTests(SignedTokenMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "local", "local@example.com", "test-password-123", first_name="Local", last_name="Name",
        )
        cls.first = get_user_model().objects.create_user("unrelated")
        SocialAccount.objects.create(
            user=cls.first, provider="entraid-work", uid="first-sub",
            extra_data={"id_token": {"oid": "another-object", "tid": TENANT}},
        )
        cls.account = SocialAccount.objects.create(
            user=cls.user, provider="entraid-work", uid="oidc-sub",
            extra_data={"id_token": {"oid": OID, "tid": TENANT, "sub": "oidc-sub"}},
        )
        SocialAccount.objects.create(user=cls.user, provider="entraid-other", uid="oidc-sub")

    def setUp(self):
        cache.clear()

    def graph_login(self, *, expires_in=3600, refresh_token="session-refresh"):
        self.client.force_login(self.user)
        session = self.client.session
        request = SimpleNamespace(session=session)
        GraphTokenManager.store_token(
            request, "entraid-work", "session-access", refresh_token, expires_in,
            TENANT, oid=OID,
        )
        session[AUTH_SOURCE_SESSION_KEY] = "entraid-work"
        session.save()

    def sso(self, token=None):
        return self.client.post(
            "/api/teams/sso/", {"provider_id": "entraid-work", "token": token or self.token()},
            content_type="application/json",
        )

    @patch("crm.graph.requests.get")
    def test_normal_allauth_login_captures_real_socialtoken_by_provider_name(self, get):
        self.assertEqual(self.client.get("/test/oidc/entraid-work/login/").status_code, 302)
        tokens = self.client.session["graph_tokens"]["entraid-work"]
        self.assertEqual(tokens["access_token"], "normal-access")
        self.assertEqual(tokens["refresh_token"], "normal-refresh")
        self.assertEqual(tokens["tenant_id"], TENANT)
        self.assertEqual(tokens["oid"], OID)
        get.return_value = response(PROFILE)
        data = self.client.get("/api/session/").json()
        self.assertEqual(data["user"]["name"], "Graph Name")
        self.assertTrue(data["user"]["graph_enabled"])
        self.assertEqual(data["user"]["email"], "graph@example.com")
        self.assertEqual(data["user"]["picture_url"], "/api/graph/picture/")
        self.assertNotIn("normal-access", str(data))
        self.assertFalse(SocialToken.objects.exists())
        self.account.refresh_from_db()
        self.assertNotIn("access_token", str(self.account.extra_data))
        self.assertNotIn("refresh_token", str(self.account.extra_data))

    def test_token_capture_preserves_exact_expiry(self):
        provider = get_adapter().get_provider(None, "entraid-work")
        expiry = timezone.now() + timedelta(minutes=17)
        sociallogin = provider.sociallogin_from_response(None, {"id_token": {
            "sub": "oidc-sub", "oid": OID, "tid": TENANT,
            "access_token": "must-not-use-extra-data",
        }})
        sociallogin.token = SocialToken(token="real-token", token_secret="real-refresh", expires_at=expiry)
        request = RequestFactory().get("/")
        request.session = {}
        allauth_user_logged_in.send(
            sender=get_user_model(), request=request, user=self.user, sociallogin=sociallogin,
        )
        self.assertEqual(request.session["graph_tokens"]["entraid-work"]["expires_at"], expiry.isoformat())
        self.assertEqual(request.session["graph_tokens"]["entraid-work"]["access_token"], "real-token")

    def test_provider_id_prefix_does_not_enable_other_named_provider(self):
        self.client.get("/test/oidc/entraid-other/login/")
        self.assertNotIn("graph_tokens", self.client.session)
        self.assertNotIn("graph_enabled", self.client.session)

    def test_local_login_and_provider_switch_clear_graph_credentials(self):
        self.graph_login()
        result = self.client.post("/api/login/", {
            "username": "local", "password": "test-password-123",
        }, content_type="application/json")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("graph_tokens", self.client.session)
        self.assertEqual(result.json()["user"]["auth_source"], "Local")
        self.graph_login()
        self.client.get("/test/oidc/entraid-other/login/")
        self.assertNotIn("graph_tokens", self.client.session)
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "entraid-other")

    def test_account_connect_does_not_replace_current_tokens_or_source(self):
        self.graph_login()
        before = dict(self.client.session)
        self.assertEqual(self.client.get("/test/oidc/entraid-other/connect/").status_code, 302)
        self.assertEqual(self.client.session["graph_tokens"], before["graph_tokens"])
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "entraid-work")

    def test_logout_clears_graph_credentials_and_flags(self):
        self.graph_login()
        self.client.post("/api/logout/")
        for key in ("graph_tokens", "graph_enabled", "crm_teams_authenticated", AUTH_SOURCE_SESSION_KEY):
            self.assertNotIn(key, self.client.session)

    @patch("crm.graph.requests.get")
    def test_graph_profile_and_picture_are_read_only(self, get):
        self.graph_login()
        get.return_value = response(PROFILE)
        data = self.client.get("/api/profile/").json()
        self.assertTrue(data["read_only"])
        self.assertEqual(data["first_name"], "Graph")
        self.assertEqual(data["email"], "graph@example.com")
        for path, method in (
            ("/api/profile/", "patch"), ("/api/profile/picture/", "post"),
            ("/api/profile/picture/", "delete"),
        ):
            with self.subTest(path=path, method=method):
                result = getattr(self.client, method)(path, {}, content_type="application/json")
                self.assertEqual(result.status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Local")

    @patch("crm.graph.requests.get")
    def test_graph_profile_email_falls_back_to_upn(self, get):
        self.graph_login()
        get.return_value = response({**PROFILE, "mail": None})
        self.assertEqual(self.client.get("/api/profile/").json()["email"], "upn@example.com")

    @patch("crm.graph.requests.get")
    def test_photo_is_binary_not_stream_of_integer_strings(self, get):
        self.graph_login()
        photo = b"\xff\xd8\x00\xff\x01"
        get.return_value = response(content=photo)
        for path in ("/api/profile/picture/", "/api/graph/picture/"):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.content, photo)
            self.assertEqual(result["Cache-Control"], "private, no-store")
        get.return_value = response(status=404)
        self.assertEqual(self.client.get("/api/graph/picture/").status_code, 404)

    @patch("crm.graph.requests.get")
    def test_graph_errors_do_not_trigger_session_expired_contract(self, get):
        self.graph_login()
        UserProfile.objects.create(user=self.user, picture="existing-local-picture.jpg")
        for status, expected in ((401, 403), (403, 403), (302, 502), (500, 502)):
            with self.subTest(status=status):
                get.return_value = response({"error": {"message": "session-access"}}, status)
                result = self.client.get("/api/graph/profile/")
                self.assertEqual(result.status_code, expected)
                self.assertNotIn("session-access", str(result.json()))
                self.assertNotIn("session_expired", result.json())
                self.assertIn("_auth_user_id", self.client.session)
                data = self.client.get("/api/session/").json()
                self.assertEqual(data["user"]["name"], "Local Name")
                self.assertIsNone(data["user"]["picture_url"])
                self.assertIn("graph_error", data)
                self.assertTrue(data["user"]["read_only"])
                fallback = self.client.get("/api/profile/").json()
                self.assertTrue(fallback["read_only"])
                self.assertIsNone(fallback["picture_url"])
                self.assertIn("graph_error", fallback)

    @patch("crm.graph.requests.get")
    def test_graph_personal_responses_are_private_and_not_stored(self, get):
        self.graph_login()
        for path, data in (
            ("/api/session/", PROFILE), ("/api/profile/", PROFILE),
            ("/api/graph/profile/", PROFILE), ("/api/graph/inbox/", {"value": []}),
            ("/api/graph/calendar/", {"value": []}), ("/api/graph/presence/", {}),
        ):
            with self.subTest(path=path):
                get.return_value = response(data)
                result = self.client.get(path)
                self.assertEqual(result.status_code, 200)
                self.assertIn("private", result["Cache-Control"])
                self.assertIn("no-store", result["Cache-Control"])
                get.return_value = response(status=500)
                failure = self.client.get(path)
                self.assertIn("private", failure["Cache-Control"])
                self.assertIn("no-store", failure["Cache-Control"])

    @patch("crm.graph.requests.get")
    def test_graph_me_identity_mismatch_is_denied(self, get):
        self.graph_login()
        get.return_value = response({**PROFILE, "id": "different-object"})
        self.assertEqual(self.client.get("/api/graph/profile/").status_code, 403)

    def test_anonymous_graph_requests_require_login(self):
        for endpoint in ("profile", "picture", "calendar", "inbox", "presence"):
            self.assertEqual(self.client.get(f"/api/graph/{endpoint}/").status_code, 401)

    def test_graph_client_only_uses_current_auth_provider(self):
        self.graph_login()
        session = self.client.session
        session[AUTH_SOURCE_SESSION_KEY] = "entraid-other"
        session.save()
        with patch("crm.graph.requests.get") as get:
            self.assertEqual(self.client.get("/api/graph/inbox/").status_code, 403)
        get.assert_not_called()

    @patch("crm.graph.requests.get")
    def test_calendar_input_validation_and_utc_contract(self, get):
        self.graph_login()
        get.return_value = response({"value": []})
        for start, end in (
            ("bad", "2026-10-11T12:00:00Z"),
            ("2026-10-10T12:00:00", "2026-10-11T12:00:00Z"),
            ("2026-10-12T12:00:00Z", "2026-10-11T12:00:00Z"),
            ("2026-01-01T12:00:00Z", "2028-01-01T12:00:00Z"),
        ):
            with self.subTest(start=start):
                self.assertEqual(self.client.get("/api/graph/calendar/", {
                    "start": start, "end": end,
                }).status_code, 400)
        get.assert_not_called()
        result = self.client.get("/api/graph/calendar/", {
            "start": "2026-10-10T12:00:00+02:00", "end": "2026-10-11T12:00:00+02:00",
        })
        self.assertEqual(result.json(), {"events": [], "next_page": None})
        self.assertEqual(get.call_args.kwargs["params"]["startDateTime"], "2026-10-10T10:00:00+00:00")

    @patch("crm.graph.requests.get")
    def test_inbox_endpoint_accepts_signed_page_not_arbitrary_url(self, get):
        self.graph_login()
        get.return_value = response({
            "value": [], "@odata.nextLink":
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=25",
        })
        page = self.client.get("/api/graph/inbox/").json()["next_page"]
        get.return_value = response({"value": []})
        self.assertEqual(self.client.get("/api/graph/inbox/", {"page": page}).json(),
                         {"emails": [], "next_page": None})
        self.assertEqual(get.call_args.kwargs["params"]["$skip"], "25")
        get.reset_mock()
        self.assertEqual(self.client.get("/api/graph/inbox/", {
            "page": "https://evil.example.com/steal",
        }).status_code, 400)
        get.assert_not_called()

    @patch("crm.teams.requests.post")
    @patch("crm.graph.requests.get")
    def test_refresh_rotates_session_credentials_and_checks_identity(self, get, post):
        self.graph_login(expires_in=-1)
        get.return_value = response(PROFILE)
        post.return_value = response({
            "access_token": "renewed-access", "refresh_token": "renewed-refresh",
            "token_type": "Bearer", "expires_in": 3600,
        })
        self.assertEqual(self.client.get("/api/graph/profile/").status_code, 200)
        tokens = self.client.session["graph_tokens"]["entraid-work"]
        self.assertEqual(tokens["access_token"], "renewed-access")
        self.assertEqual(tokens["refresh_token"], "renewed-refresh")
        self.assertEqual(post.call_args.args[0],
                         f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token")
        self.assertEqual(post.call_args.kwargs["data"]["scope"],
                         "https://graph.microsoft.com/.default offline_access")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer " + "renewed-access")
        self.client.get("/api/graph/profile/")
        post.assert_called_once()

    @patch("crm.teams.requests.post")
    @patch("crm.graph.requests.get")
    def test_refresh_preserves_old_refresh_token_when_not_rotated(self, get, post):
        self.graph_login(expires_in=-1)
        get.return_value = response(PROFILE)
        post.return_value = response({
            "access_token": "renewed-access", "token_type": "Bearer", "expires_in": 3600,
        })
        self.assertEqual(self.client.get("/api/graph/profile/").status_code, 200)
        self.assertEqual(self.client.session["graph_tokens"]["entraid-work"]["refresh_token"], "session-refresh")

    @patch("crm.teams.requests.post")
    def test_expired_without_refresh_and_tenant_mismatch_never_exchange(self, post):
        self.graph_login(expires_in=-1, refresh_token="")
        self.assertEqual(self.client.get("/api/graph/profile/").status_code, 403)
        self.graph_login()
        session = self.client.session
        session["graph_tokens"]["entraid-work"]["tenant_id"] = "other-tenant"
        session.save()
        self.assertEqual(self.client.get("/api/graph/profile/").status_code, 403)
        post.assert_not_called()
        self.assertNotIn("graph_tokens", self.client.session)
        self.assertEqual(self.client.patch("/api/profile/", {
            "first_name": "Overwrite",
        }, content_type="application/json").status_code, 403)

    @patch("crm.teams.requests.post")
    @patch("crm.graph.requests.get")
    def test_refresh_failure_redirect_or_mismatch_does_not_expose_credentials(self, get, post):
        for status, expected in ((400, 403), (302, 502), (500, 502)):
            with self.subTest(status=status):
                self.graph_login(expires_in=-1)
                post.return_value = response({"error_description": "session-refresh"}, status)
                result = self.client.get("/api/graph/profile/")
                self.assertEqual(result.status_code, expected)
                self.assertNotIn("session-refresh", str(result.json()))
        self.graph_login(expires_in=-1)
        post.return_value = response({
            "access_token": "renewed-access", "token_type": "Bearer", "expires_in": 3600,
        })
        get.return_value = response({**PROFILE, "id": "another-user"})
        self.assertEqual(self.client.get("/api/graph/profile/").status_code, 403)
        self.assertNotIn("graph_tokens", self.client.session)

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    @patch("crm.graph.requests.get")
    def test_sso_second_account_nested_identity_different_sub_and_obo(self, get, post, keys):
        keys.return_value = self.jwks
        post.return_value = response({
            "access_token": "obo-access", "refresh_token": "obo-refresh",
            "token_type": "Bearer", "expires_in": 3600,
        })
        get.return_value = response(PROFILE)
        token = self.token()
        result = self.sso(token)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["user"]["id"], self.user.pk)
        self.assertEqual(result.json()["user"]["name"], "Graph Name")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "entraid-work")
        self.assertTrue(self.client.session["crm_teams_authenticated"])
        self.assertEqual(self.client.session["graph_tokens"]["entraid-work"]["access_token"], "obo-access")
        data = post.call_args.kwargs["data"]
        self.assertEqual(data["assertion"], token)
        self.assertEqual(data["requested_token_use"], "on_behalf_of")
        self.assertEqual(data["scope"], "https://graph.microsoft.com/.default offline_access")
        self.assertEqual(post.call_args.args[0],
                         f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token")
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)
        self.assertNotIn("obo-access", str(result.json()))
        self.assertEqual(SocialAccount.objects.count(), 3)
        self.account.refresh_from_db()
        self.assertEqual(self.account.extra_data, {
            "id_token": {"oid": OID, "tid": TENANT, "sub": "oidc-sub"},
        })

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    def test_sso_inactive_unlinked_and_same_email_do_not_authenticate(self, post, keys):
        keys.return_value = self.jwks
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.sso().status_code, 403)
        self.user.is_active = True
        self.user.save()
        self.account.delete()
        claims = {**self.claims(), "email": self.user.email}
        self.assertEqual(self.sso(self.token(claims)).status_code, 403)
        post.assert_not_called()
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    @patch("crm.graph.requests.get")
    def test_sso_obo_graph_identity_mismatch_does_not_login(self, get, post, keys):
        keys.return_value = self.jwks
        post.return_value = response({
            "access_token": "obo-access", "token_type": "Bearer", "expires_in": 3600,
        })
        get.return_value = response({**PROFILE, "id": "different-object"})
        self.assertEqual(self.sso().status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("graph_tokens", self.client.session)

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    def test_sso_obo_errors_are_403_or_502_without_remote_token_text(self, post, keys):
        keys.return_value = self.jwks
        for status, expected in ((400, 403), (302, 502), (500, 502)):
            with self.subTest(status=status):
                post.return_value = response({"error_description": "secret-value"}, status)
                result = self.sso()
                self.assertEqual(result.status_code, expected)
                self.assertNotIn("secret-value", str(result.json()))
                self.assertNotIn("_auth_user_id", self.client.session)

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    def test_sso_malformed_obo_token_responses_never_login(self, post, keys):
        keys.return_value = self.jwks
        good = {"access_token": "obo-access", "token_type": "Bearer", "expires_in": 3600}
        for token in (
            [], {}, {**good, "access_token": ""},
            {**good, "token_type": "other"}, {**good, "token_type": 1},
            {**good, "expires_in": -1}, {**good, "expires_in": True},
            {**good, "expires_in": 10 ** 100}, {**good, "refresh_token": {}},
        ):
            with self.subTest(token=token):
                post.return_value = response(token)
                self.assertEqual(self.sso().status_code, 502)
                self.assertNotIn("_auth_user_id", self.client.session)

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    def test_duplicate_linked_identity_is_rejected_without_obo(self, post, keys):
        keys.return_value = self.jwks
        SocialAccount.objects.create(
            user=self.first, provider="entraid-work", uid="duplicate-sub",
            extra_data={"oid": OID, "tid": TENANT},
        )
        self.assertEqual(self.sso().status_code, 403)
        post.assert_not_called()

    def test_teams_config_is_public_and_name_based_without_secrets(self):
        result = self.client.get("/api/teams/config/")
        self.assertEqual(result.json(), {"graph_only": True, "providers": [{
            "id": "entraid-work", "provider_id": "entraid-work", "name": "entraid-work", "client_id": CLIENT,
            "resource": f"api://portal.example.com/{CLIENT}",
            "tenant_id": TENANT,
        }]})
        self.assertNotIn("fixture", str(result.json()))
        self.assertIn("csrftoken", result.cookies)

    @override_settings(TEAMS_GRAPH_ONLY=False)
    def test_teams_config_can_select_legacy_startup(self):
        self.assertFalse(self.client.get("/api/teams/config/").json()["graph_only"])

    @patch("crm.teams._get_jwks")
    @patch("crm.views.exchange_graph_token")
    @patch("crm.views.GraphClient")
    def test_identity_verification_needs_no_portal_account_or_obo(self, graph, exchange, keys):
        keys.return_value = self.jwks
        SocialAccount.objects.all().delete()
        result = self.client.post(
            "/api/teams/identity/", {"provider_id": "entraid-work", "token": self.token()},
            content_type="application/json",
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"id": OID, "tenant_id": TENANT})
        self.assertIn("no-store", result["Cache-Control"])
        exchange.assert_not_called()
        graph.assert_not_called()
        for key in ("_auth_user_id", "graph_tokens", AUTH_SOURCE_SESSION_KEY, "crm_teams_authenticated"):
            self.assertNotIn(key, self.client.session)
        self.assertEqual(self.client.get("/api/contacts/").status_code, 401)

    @patch("crm.teams._get_jwks")
    def test_identity_verification_preserves_existing_browser_session(self, keys):
        keys.return_value = self.jwks
        self.graph_login()
        before = dict(self.client.session)
        result = self.client.post(
            "/api/teams/identity/", {"provider_id": "entraid-work", "token": self.token()},
            content_type="application/json",
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(dict(self.client.session), before)

    @patch("crm.teams._get_jwks")
    def test_identity_verification_requires_csrf_and_valid_claims(self, keys):
        keys.return_value = self.jwks
        client = Client(enforce_csrf_checks=True)
        config = client.get("/api/teams/config/")
        data = {"provider_id": "entraid-work", "token": self.token()}
        self.assertEqual(client.post(
            "/api/teams/identity/", data, content_type="application/json",
        ).status_code, 403)
        headers = {"HTTP_X_CSRFTOKEN": config.cookies["csrftoken"].value}
        self.assertEqual(client.post(
            "/api/teams/identity/", data, content_type="application/json", **headers,
        ).status_code, 200)
        for claim, value in (("aud", "wrong"), ("tid", CLIENT), ("scp", "User.Read"), ("exp", 1)):
            with self.subTest(claim=claim):
                result = client.post(
                    "/api/teams/identity/",
                    {"provider_id": "entraid-work", "token": self.token({**self.claims(), claim: value})},
                    content_type="application/json", **headers,
                )
                self.assertEqual(result.status_code, 403)
                self.assertIn("no-store", result["Cache-Control"])

    @patch("crm.views.ratelimit.consume", return_value=False)
    @patch("crm.teams._get_jwks")
    def test_identity_verification_is_rate_limited(self, keys, consume):
        result = self.client.post(
            "/api/teams/identity/", {"provider_id": "entraid-work", "token": self.token()},
            content_type="application/json",
        )
        self.assertEqual(result.status_code, 429)
        keys.assert_not_called()

    @patch("crm.teams._get_jwks")
    def test_identity_verification_binds_multitenant_issuer_to_signed_tenant(self, keys):
        keys.return_value = self.jwks
        providers = configure_oidc(json.dumps([{
            "id": "entraid-work", "name": "entraid-work",
            "server_url": "https://login.microsoftonline.com/organizations/v2.0",
            "client_id": CLIENT, "client_secret": "fixture",
            "teams": {"resource": f"api://portal.example.com/{CLIENT}"},
        }]), debug=False)
        with override_settings(SOCIALACCOUNT_PROVIDERS=providers):
            result = self.client.post(
                "/api/teams/identity/", {"provider_id": "entraid-work", "token": self.token()},
                content_type="application/json",
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"id": OID, "tenant_id": TENANT})

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    @patch("crm.graph.requests.get")
    def test_fresh_teams_config_cookie_supports_csrf_protected_sso(self, get, post, keys):
        keys.return_value = self.jwks
        get.return_value = response(PROFILE)
        post.return_value = response({
            "access_token": "obo-access", "token_type": "Bearer", "expires_in": 3600,
        })
        client = Client(enforce_csrf_checks=True)
        config = client.get("/api/teams/config/")
        data = {"provider_id": "entraid-work", "token": self.token()}
        self.assertEqual(client.post(
            "/api/teams/sso/", data, content_type="application/json",
        ).status_code, 403)
        result = client.post(
            "/api/teams/sso/", data, content_type="application/json",
            HTTP_X_CSRFTOKEN=config.cookies["csrftoken"].value,
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["user"]["id"], self.user.pk)
        self.assertEqual(result.json()["teams_identity"], {"id": OID, "tenant_id": TENANT})

    @patch("crm.teams._get_jwks")
    @patch("crm.teams.requests.post")
    def test_sso_rejects_short_or_oversized_input_before_token_verification(self, post, keys):
        for token in ("", "x" * 255, "x" * 16385, "x" * 32769):
            with self.subTest(length=len(token)):
                result = self.client.post(
                    "/api/teams/sso/", {"provider_id": "entraid-work", "token": token},
                    content_type="application/json",
                )
                self.assertEqual(result.status_code, 400)
        keys.assert_not_called()
        post.assert_not_called()

    @patch("crm.teams._get_jwks")
    def test_identity_rejects_short_or_oversized_input_and_unknown_provider(self, keys):
        for token in ("", "x" * 255, "x" * 16385, "x" * 32769):
            result = self.client.post(
                "/api/teams/identity/", {"provider_id": "entraid-work", "token": token},
                content_type="application/json",
            )
            self.assertEqual(result.status_code, 400)
        result = self.client.post(
            "/api/teams/identity/", {"provider_id": "missing", "token": self.token()},
            content_type="application/json",
        )
        self.assertEqual(result.status_code, 404)
        keys.assert_not_called()
