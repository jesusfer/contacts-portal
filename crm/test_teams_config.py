import json

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from config.settings import GRAPH_SCOPES, TEAMS_CLIENT_IDS, configure_oidc
from .middleware import TeamsFrameMiddleware


class TeamsConfigurationTests(SimpleTestCase):
    def provider(self, **changes):
        data = {
            "id": "work", "name": "entraid-work",
            "client_id": "11111111-1111-1111-1111-111111111111",
            "client_secret": "fixture",
            "server_url": "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/v2.0",
        }
        data.update(changes)
        return data

    def configure(self, provider):
        return configure_oidc(json.dumps([provider]), debug=False)["openid_connect"]["APPS"][0]

    def test_entraid_graph_scope_defaults(self):
        app = self.configure(self.provider())
        self.assertEqual(app["settings"]["scope"], GRAPH_SCOPES)
        self.assertEqual(app["settings"]["graph_tenant_id"], "22222222-2222-2222-2222-222222222222")
        self.assertNotIn("teams", app["settings"])

    def test_teams_defaults(self):
        app = self.configure(self.provider(teams={
            "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111",
        }))
        self.assertEqual(app["settings"]["teams"]["authorized_client_ids"], TEAMS_CLIENT_IDS)
        self.assertEqual(app["settings"]["teams"]["audience"], app["client_id"])

    def test_organizations_issuer_defaults_and_teams(self):
        app = self.configure(self.provider(
            server_url="https://login.microsoftonline.com/organizations/v2.0",
            teams={"resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111"},
        ))
        self.assertEqual(app["settings"]["graph_tenant_id"], "organizations")
        self.assertEqual(app["settings"]["teams"]["tenant_id"], "organizations")

    def test_graph_selection_uses_name_not_identifier(self):
        app = self.configure(self.provider(id="corporate", name="ENTRAID-corporate"))
        self.assertEqual(app["settings"]["scope"], GRAPH_SCOPES)
        ordinary = self.configure(self.provider(
            id="entraid-work", name="Other", server_url="https://identity.example.com",
        ))
        self.assertNotIn("scope", ordinary["settings"])

    def test_reject_invalid_teams_settings(self):
        for teams in (
            {},
            {"resource": "https://portal.example.com"},
            {"resource": "api://portal.example.com/wrong"},
            {"resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111", "audience": "other"},
            {"resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111", "tenant_id": "33333333-3333-3333-3333-333333333333"},
            {"resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111", "authorized_client_ids": []},
            {"unexpected": True},
        ):
            with self.subTest(teams=teams), self.assertRaises(ImproperlyConfigured):
                self.configure(self.provider(teams=teams))

    def test_reject_non_tenant_entraid_issuers(self):
        for issuer in (
            "https://identity.example.com",
            "https://login.microsoftonline.com/common/v2.0",
            "https://login.microsoftonline.com/consumers/v2.0",
            "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222",
        ):
            with self.subTest(issuer=issuer), self.assertRaises(ImproperlyConfigured):
                self.configure(self.provider(server_url=issuer))

    def test_graph_scopes_cannot_be_removed(self):
        with self.assertRaises(ImproperlyConfigured):
            self.configure(self.provider(scope=["openid"]))

    def test_other_oidc_providers_keep_existing_behavior(self):
        app = self.configure(self.provider(name="Other", server_url="https://identity.example.com"))
        self.assertEqual(app["settings"], {"server_url": "https://identity.example.com"})
        with self.assertRaises(ImproperlyConfigured):
            self.configure(self.provider(name="Other", teams={}))


@override_settings(TEAMS_ENABLED=True)
class TeamsFrameTests(SimpleTestCase):
    def response(self, path, *, teams_session=False):
        request = RequestFactory().get(path)
        request.session = {"crm_teams_authenticated": teams_session}

        def downstream(request):
            response = HttpResponse()
            response["X-Frame-Options"] = "DENY"
            response["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
            return response

        return TeamsFrameMiddleware(downstream)(request)

    def test_tab_has_restricted_frame_policy(self):
        for path in ("/teams/tab/", "/teams/config/"):
            response = self.response(path)
            self.assertNotIn("X-Frame-Options", response)
            self.assertIn("https://*.cloud.microsoft", response["Content-Security-Policy"])
            self.assertIn("default-src 'self'", response["Content-Security-Policy"])
            self.assertNotIn("frame-ancestors 'none'", response["Content-Security-Policy"])

    def test_ordinary_routes_and_admin_keep_frame_protection(self):
        for path, session in (("/", False), ("/accounts/login/", True), ("/admin/", True)):
            self.assertEqual(self.response(path, teams_session=session)["X-Frame-Options"], "DENY")
        self.assertEqual(self.response("/", teams_session=True)["X-Frame-Options"], "DENY")

    @override_settings(TEAMS_ENABLED=False)
    def test_disabled_teams_keep_frame_protection(self):
        self.assertEqual(self.response("/teams/tab/")["X-Frame-Options"], "DENY")
