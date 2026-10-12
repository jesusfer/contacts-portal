import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
from allauth.socialaccount.models import SocialAccount
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .api_messaging_extension import RESULT_LIMIT, SUMMARY_LIMIT
from .messaging_extension import CARD_BYTE_LIMIT
from .models import Agent, Appointment, Call, Company, Contact


TENANT = "22222222-2222-2222-2222-222222222222"
OID = "33333333-3333-3333-3333-333333333333"
APP_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "1fec8e78-bce4-4aaf-ab1b-5451cc387264"
PROVIDER = "entraid-work"
ENDPOINT = "/api/teams/contact-search/"
CONFIG = {
    "tenant_id": TENANT, "audience": APP_ID,
    "resource": f"api://portal.example.com/{APP_ID}", "authorized_client_ids": [CLIENT_ID],
}
PROVIDERS = {"openid_connect": {"APPS": [{
    "provider_id": PROVIDER, "client_id": APP_ID, "secret": "fixture",
    "settings": {
        "server_url": f"https://login.microsoftonline.com/{TENANT}/v2.0", "teams": CONFIG,
    },
}]}}


@override_settings(
    SECURE_SSL_REDIRECT=False, TEAMS_API_PROVIDER_ID=PROVIDER,
    SOCIALACCOUNT_PROVIDERS=PROVIDERS,
)
class APIContactSearchTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key(), as_dict=True)
        cls.jwks = {"keys": [{**jwk, "kid": "test-key"}]}

    def setUp(self):
        self.user = get_user_model().objects.create_user("agent")
        self.agent = Agent.objects.create(user=self.user)
        self.account = SocialAccount.objects.create(
            user=self.user, provider=PROVIDER, uid="oidc-subject",
            extra_data={"id_token": {"oid": OID, "tid": TENANT}},
        )
        self.company = Company.objects.create(name="Acme")
        self.contact = Contact.objects.create(
            name="Alice", email="alice@example.com", company=self.company,
        )
        self.client = Client(enforce_csrf_checks=True)
        patcher = patch("crm.teams._get_jwks", return_value=self.jwks)
        self.keys = patcher.start()
        self.addCleanup(patcher.stop)

    def token(self, **changes):
        now = int(timezone.now().timestamp())
        claims = {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": APP_ID, "exp": now + 3600, "nbf": now - 1, "iat": now - 1,
            "tid": TENANT, "oid": OID, "sub": "access-subject",
            "scp": "access_as_user", "azp": CLIENT_ID,
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})

    def search(self, query="acme", token=None):
        return self.client.get(
            ENDPOINT, {"query": query}, HTTP_AUTHORIZATION=" ".join(("Bearer", token or self.token())),
        )

    def test_search_matches_customer_contact_email_and_combined_words(self):
        for query in ("ACME", "ali", "example.com", "acme alice", "alice acme"):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["contacts"], [{
                    "name": "Alice", "email": r"alice@example\.com",
                    "customer": "Acme", "calls": "No calls recorded.",
                }])
                self.assertEqual(result["Cache-Control"], "private, no-store")
        self.assertEqual(self.search("unknown").json(), {"contacts": []})

    def test_last_three_calls_are_newest_first_and_agent_scoped(self):
        other = Agent.objects.create(user=get_user_model().objects.create_user("other"))
        for summary in ("Oldest", "Second", "Third", "Newest"):
            Call.objects.create(
                agent=self.agent, contact=self.contact, status=Call.Status.COMPLETED,
                summary=summary, rating=4,
            )
        Call.objects.create(agent=other, contact=self.contact, summary="Private other agent")
        different = Contact.objects.create(name="Bob", email="bob@example.com", company=self.company)
        Call.objects.create(agent=self.agent, contact=different, summary="Private other contact")
        latest = self.agent.calls.filter(contact=self.contact).first()
        start = timezone.now() - timedelta(days=1)
        Appointment.objects.create(call=latest, starts_at=start, ends_at=start + timedelta(hours=1))
        with self.assertNumQueries(4):
            result = self.search()
        history = result.json()["contacts"][0]["calls"]
        self.assertLess(history.index("Newest"), history.index("Third"))
        self.assertLess(history.index("Third"), history.index("Second"))
        self.assertNotIn("Oldest", history)
        self.assertNotIn("Private", history)
        self.assertIn("Completed", history)
        self.assertIn("Rating: 4/5", history)
        self.assertIn(start.strftime("%Y\\-%m\\-%d %H:%M UTC"), history)

    def test_empty_short_and_long_histories_and_untrusted_markdown(self):
        self.contact.name = "[Alice](https://evil.example.com)"
        self.contact.save()
        Call.objects.create(
            agent=self.agent, contact=self.contact,
            summary="*" + "x" * (SUMMARY_LIMIT + 10),
        )
        result = self.search().json()["contacts"][0]
        self.assertEqual(result["name"], r"\[Alice\]\(https://evil\.example\.com\)")
        self.assertIn(r"\*", result["calls"])
        self.assertIn("summary shortened", result["calls"])
        self.assertNotIn("x" * SUMMARY_LIMIT, result["calls"])

    def test_maximum_unicode_fields_and_summaries_fit_a_teams_card(self):
        self.company.name = "😀" * 200
        self.company.save()
        self.contact.name = "😀" * 200
        self.contact.save()
        for index in range(3):
            Call.objects.create(
                agent=self.agent, contact=self.contact,
                summary="😀" * 1000, status=Call.Status.COMPLETED, rating=5,
            )
        row = self.search("example.com").json()["contacts"][0]
        template = json.loads(
            (Path(settings.BASE_DIR) / "teams/contact-card-template.json").read_text()
        )["responseCardTemplate"]

        def render(value):
            if isinstance(value, dict):
                return {key: render(item) for key, item in value.items()}
            if isinstance(value, list):
                return [render(item) for item in value]
            if isinstance(value, str) and value.startswith("${"):
                return row[value[2:-1]]
            return value

        self.assertLess(len(json.dumps(render(template)).encode("utf-8")), CARD_BYTE_LIMIT)
        self.assertEqual(row["calls"].count("summary shortened"), 3)

    def test_results_are_bounded_with_shared_directory_access(self):
        Contact.objects.bulk_create([
            Contact(name=f"Contact {index:03}", email=f"c{index}@example.com", company=self.company)
            for index in range(RESULT_LIMIT + 5)
        ])
        self.assertFalse(self.agent.companies.exists())
        with self.assertNumQueries(4):
            contacts = self.search().json()["contacts"]
        self.assertEqual(len(contacts), RESULT_LIMIT)
        self.assertEqual(contacts[0]["name"], "Alice")

    def test_missing_bearer_rejects_even_logged_in_sessions(self):
        self.client.force_login(self.user)
        result = self.client.get(ENDPOINT, {"query": "acme"})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result["WWW-Authenticate"], "Bearer")
        self.keys.assert_not_called()

    def test_wrong_scope_audience_tenant_client_expiry_and_signature_are_rejected(self):
        for changes in (
            {"scp": "User.Read"}, {"scp": None}, {"aud": "other-app"},
            {"tid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
            {"iss": "https://evil.example.com"}, {"azp": "other-client"},
            {"exp": int(timezone.now().timestamp()) - 60},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.search(token=self.token(**changes)).status_code, 401)
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(
            jwt.decode(self.token(), options={"verify_signature": False}),
            wrong_key, algorithm="RS256", headers={"kid": "test-key"},
        )
        self.assertEqual(self.search(token=token).status_code, 401)
        self.assertEqual(self.search(token="not-a-token").status_code, 401)

    def test_unlinked_inactive_unprovisioned_and_ambiguous_accounts_are_rejected(self):
        self.assertEqual(self.search(token=self.token(oid="unknown")).status_code, 403)
        self.assertEqual(self.search(token=self.token(scp="access_as_user", roles=["Admin"])).status_code, 200)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.search().status_code, 403)
        self.user.is_active = True
        self.user.save()
        self.agent.delete()
        self.assertEqual(self.search().status_code, 403)
        self.agent = Agent.objects.create(user=self.user)
        SocialAccount.objects.create(
            user=get_user_model().objects.create_user("duplicate"),
            provider=PROVIDER, uid="another-subject", extra_data=self.account.extra_data,
        )
        self.assertEqual(self.search().status_code, 403)

    def test_disabled_and_misconfigured_extension_fails_closed(self):
        with override_settings(TEAMS_API_PROVIDER_ID=""):
            self.assertEqual(self.search().status_code, 503)
        with override_settings(SOCIALACCOUNT_PROVIDERS={"openid_connect": {"APPS": []}}):
            self.assertEqual(self.search().status_code, 503)
        providers = json.loads(json.dumps(PROVIDERS))
        del providers["openid_connect"]["APPS"][0]["settings"]["teams"]
        with override_settings(SOCIALACCOUNT_PROVIDERS=providers):
            self.assertEqual(self.search().status_code, 503)

    def test_invalid_query_and_methods(self):
        for query in ("", "  ", "x" * 201, " " * 201 + "acme", ["acme", "alice"]):
            with self.subTest(query=query):
                self.assertEqual(self.search(query).status_code, 400)
        self.assertEqual(self.search("x" * 200).status_code, 200)
        self.assertEqual(self.client.get(
            ENDPOINT, HTTP_AUTHORIZATION=" ".join(("Bearer", self.token())),
        ).status_code, 400)
        self.assertEqual(Client().post(ENDPOINT).status_code, 405)


class APIExtensionPackageTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / "teams"

    def test_schema_describes_only_the_contact_search_operation(self):
        spec = json.loads((self.root / "contact-search-openapi.json").read_text())
        self.assertEqual(spec["openapi"], "3.0.1")
        self.assertEqual(set(spec["paths"]), {ENDPOINT})
        self.assertEqual(set(spec["paths"][ENDPOINT]), {"get"})
        operation = spec["paths"][ENDPOINT]["get"]
        self.assertEqual(operation["operationId"], "searchContacts")
        self.assertEqual([param["name"] for param in operation["parameters"]], ["query"])
        properties = operation["responses"]["200"]["content"]["application/json"]["schema"][
            "properties"
        ]["contacts"]["items"]["properties"]
        self.assertEqual(set(properties), {"name", "email", "customer", "calls"})
        self.assertTrue(all(value["type"] == "string" for value in properties.values()))

    def test_manifest_and_template_are_connected_to_the_api_not_the_bot(self):
        manifest = json.loads((self.root / "api-manifest.json").read_text())
        extension, = manifest["composeExtensions"]
        self.assertEqual(extension["composeExtensionType"], "apiBased")
        self.assertNotIn("botId", extension)
        self.assertEqual(extension["apiSpecificationFile"], "contact-search-openapi.json")
        self.assertEqual(extension["authorization"]["authType"], "microsoftEntra")
        command, = extension["commands"]
        self.assertEqual(command["id"], "searchContacts")
        self.assertEqual(command["apiResponseRenderingTemplateFile"], "contact-card-template.json")
        template = json.loads((self.root / command["apiResponseRenderingTemplateFile"]).read_text())
        self.assertEqual(template["responseLayout"], "list")
        self.assertEqual(template["jsonPath"], "contacts")
        self.assertEqual(template["responseCardTemplate"]["type"], "AdaptiveCard")
        text = json.dumps(template["responseCardTemplate"])
        for field in ("name", "email", "customer", "calls"):
            self.assertIn("${" + field + "}", text)
        for icon in manifest["icons"].values():
            self.assertTrue((self.root / icon).is_file())
