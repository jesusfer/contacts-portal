import json

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.contrib.auth import get_user_model
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)

from config.settings import GRAPH_SCOPES, TEAMS_CLIENT_IDS, configure_oidc
from ..middleware import TeamsFrameMiddleware
from ..models import Agent, Call, Company, Contact
from ..signals import AUTH_SOURCE_SESSION_KEY


class TeamsConfigurationTests(SimpleTestCase):
    def provider(self, **changes):
        data = {
            "id": "entraid-work",
            "name": "Work identity",
            "client_id": "11111111-1111-1111-1111-111111111111",
            "client_secret": "fixture",
            "server_url": "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/v2.0",
        }
        data.update(changes)
        return data

    def configure(self, provider):
        return configure_oidc(json.dumps([provider]), debug=False)["openid_connect"][
            "APPS"
        ][0]

    def test_entraid_graph_scope_defaults(self):
        app = self.configure(self.provider())
        self.assertEqual(app["settings"]["scope"], GRAPH_SCOPES)
        self.assertEqual(
            app["settings"]["graph_tenant_id"], "22222222-2222-2222-2222-222222222222"
        )
        self.assertNotIn("teams", app["settings"])

    def test_teams_defaults(self):
        app = self.configure(
            self.provider(
                teams={
                    "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111",
                }
            )
        )
        self.assertEqual(
            app["settings"]["teams"]["authorized_client_ids"], TEAMS_CLIENT_IDS
        )
        self.assertEqual(app["settings"]["teams"]["audience"], app["client_id"])

    def test_teams_accepts_bot_resource(self):
        resource = "api://portal.example.com/botid-11111111-1111-1111-1111-111111111111"
        app = self.configure(self.provider(teams={"resource": resource}))
        self.assertEqual(app["settings"]["teams"]["resource"], resource)

    def test_organizations_issuer_defaults_and_teams(self):
        app = self.configure(
            self.provider(
                server_url="https://login.microsoftonline.com/organizations/v2.0",
                teams={
                    "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111"
                },
            )
        )
        self.assertEqual(app["settings"]["graph_tenant_id"], "organizations")
        self.assertEqual(app["settings"]["teams"]["tenant_id"], "organizations")

    def test_graph_selection_uses_provider_id_prefix(self):
        app = self.configure(self.provider(id="entraid-corporate", name="Corporate"))
        self.assertEqual(app["settings"]["scope"], GRAPH_SCOPES)
        ordinary = self.configure(
            self.provider(
                id="work",
                name="ENTRAID-work",
                server_url="https://identity.example.com",
            )
        )
        self.assertNotIn("scope", ordinary["settings"])

    def test_reject_invalid_teams_settings(self):
        for teams in (
            {},
            {"resource": "https://portal.example.com"},
            {"resource": "api://portal.example.com/wrong"},
            {"resource": "api://portal.example.com/botid-wrong"},
            {
                "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111",
                "audience": "other",
            },
            {
                "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111",
                "tenant_id": "33333333-3333-3333-3333-333333333333",
            },
            {
                "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111",
                "authorized_client_ids": [],
            },
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
        app = self.configure(
            self.provider(
                id="work", name="Other", server_url="https://identity.example.com"
            )
        )
        self.assertEqual(
            app["settings"], {"server_url": "https://identity.example.com"}
        )
        with self.assertRaises(ImproperlyConfigured):
            self.configure(self.provider(id="work", name="Other", teams={}))


@override_settings(TEAMS_ENABLED=True)
class TeamsFrameTests(SimpleTestCase):
    def response(self, path, *, teams_session=False):
        request = RequestFactory().get(path)
        request.session = {"crm_teams_authenticated": teams_session}

        def downstream(request):
            response = HttpResponse()
            response["X-Frame-Options"] = "DENY"
            response["Content-Security-Policy"] = (
                "default-src 'self'; frame-ancestors 'none'"
            )
            return response

        return TeamsFrameMiddleware(downstream)(request)

    def test_tab_has_restricted_frame_policy(self):
        for path in ("/teams/tab/", "/teams/config/", "/teams/customer/"):
            response = self.response(path)
            self.assertNotIn("X-Frame-Options", response)
            self.assertIn(
                "https://*.cloud.microsoft", response["Content-Security-Policy"]
            )
            self.assertIn("default-src 'self'", response["Content-Security-Policy"])
            self.assertNotIn(
                "frame-ancestors 'none'", response["Content-Security-Policy"]
            )

    def test_ordinary_routes_and_admin_keep_frame_protection(self):
        for path, session in (
            ("/", False),
            ("/accounts/login/", True),
            ("/admin/", True),
        ):
            self.assertEqual(
                self.response(path, teams_session=session)["X-Frame-Options"], "DENY"
            )
        self.assertEqual(
            self.response("/", teams_session=True)["X-Frame-Options"], "DENY"
        )

    @override_settings(TEAMS_ENABLED=False)
    def test_disabled_teams_keep_frame_protection(self):
        self.assertEqual(self.response("/teams/tab/")["X-Frame-Options"], "DENY")
        self.assertEqual(self.response("/teams/customer/")["X-Frame-Options"], "DENY")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SOCIALACCOUNT_PROVIDERS=configure_oidc(
        json.dumps(
            [
                {
                    "id": "entraid-work",
                    "name": "Work",
                    "client_id": "11111111-1111-1111-1111-111111111111",
                    "client_secret": "fixture",
                    "server_url": "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/v2.0",
                    "teams": {
                        "resource": "api://portal.example.com/11111111-1111-1111-1111-111111111111"
                    },
                }
            ]
        ),
        debug=False,
    ),
)
class TeamsCustomerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("agent")
        self.agent = Agent.objects.create(user=self.user)
        self.company = Company.objects.create(name="Customer")
        self.other_company = Company.objects.create(name="Other customer")
        self.contact = Contact.objects.create(
            name="Alice", email="alice@example.com", company=self.company
        )
        other_contact = Contact.objects.create(
            name="Bob", email="bob@example.com", company=self.other_company
        )
        self.call = Call.objects.create(agent=self.agent, contact=self.contact)
        Call.objects.create(agent=self.agent, contact=other_contact)
        peer = Agent.objects.create(user=get_user_model().objects.create_user("peer"))
        Call.objects.create(
            agent=peer, contact=self.contact, summary="Private peer call"
        )
        self.client.force_login(self.user)
        session = self.client.session
        session[AUTH_SOURCE_SESSION_KEY] = "entraid-work"
        session["crm_teams_authenticated"] = True
        session.save()

    def test_customer_contacts_are_shared_but_calls_are_agent_scoped(self):
        response = self.client.get(
            f"/api/teams/customers/{self.company.pk}/?provider=entraid-work"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["customer"], {"id": self.company.pk, "name": self.company.name}
        )
        self.assertEqual([item["id"] for item in data["contacts"]], [self.contact.pk])
        self.assertEqual([item["id"] for item in data["calls"]], [self.call.pk])
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        customers = self.client.get(
            "/api/teams/customers/?provider=entraid-work"
        ).json()["customers"]
        self.assertEqual(
            {item["id"] for item in customers}, {self.company.pk, self.other_company.pk}
        )

    def test_require_authentication_and_matching_teams_sso_session(self):
        for path in (
            "/api/teams/customers/",
            f"/api/teams/customers/{self.company.pk}/",
        ):
            self.assertEqual(
                Client().get(path + "?provider=entraid-work").status_code, 401
            )
            for provider in ("", "entraid-other"):
                self.assertEqual(
                    self.client.get(path + f"?provider={provider}").status_code, 403
                )
            session = self.client.session
            session["crm_teams_authenticated"] = False
            session.save()
            self.assertEqual(
                self.client.get(path + "?provider=entraid-work").status_code, 403
            )
            session["crm_teams_authenticated"] = True
            session.save()

    def test_unprovisioned_user_and_missing_customer(self):
        self.assertEqual(
            self.client.get(
                "/api/teams/customers/999999/?provider=entraid-work"
            ).status_code,
            404,
        )
        user = get_user_model().objects.create_user("unprovisioned")
        self.client.force_login(user)
        self.assertEqual(
            self.client.get("/api/teams/customers/?provider=entraid-work").status_code,
            403,
        )

    def test_empty_customer_and_read_only_endpoints(self):
        empty = Company.objects.create(name="Empty")
        data = self.client.get(
            f"/api/teams/customers/{empty.pk}/?provider=entraid-work"
        ).json()
        self.assertEqual(data["contacts"], [])
        self.assertEqual(data["calls"], [])
        self.assertEqual(
            self.client.post("/api/teams/customers/?provider=entraid-work").status_code,
            405,
        )
