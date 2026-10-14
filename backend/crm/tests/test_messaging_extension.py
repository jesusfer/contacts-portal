import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
from allauth.socialaccount.models import SocialAccount
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from botbuilder.schema import ResourceResponse
from botframework.connector.auth import AuthenticationConstants
from PIL import Image

from ..teams.messaging_extension.search import (
    CARD_BYTE_LIMIT,
    COMMAND_ID,
    CONTACT_LIMIT,
    PAGE_SIZE,
)
from ..models import Agent, Call, Company, Contact


TENANT = "22222222-2222-2222-2222-222222222222"
OID = "33333333-3333-3333-3333-333333333333"
APP_ID = "11111111-1111-1111-1111-111111111111"
SERVICE_URL = "https://smba.trafficmanager.net/teams/"
PROVIDER = "entraid-work"
ENDPOINT = "/api/teams/messages/"


@override_settings(
    SECURE_SSL_REDIRECT=False,
    TEAMS_BOT_APP_ID=APP_ID,
    TEAMS_BOT_APP_PASSWORD="fixture",
    TEAMS_BOT_TENANT_ID=TENANT,
    TEAMS_BOT_PROVIDER_ID=PROVIDER,
    SOCIALACCOUNT_PROVIDERS={
        "openid_connect": {
            "APPS": [
                {
                    "provider_id": PROVIDER,
                    "client_id": APP_ID,
                    "secret": "fixture",
                    "settings": {
                        "server_url": f"https://login.microsoftonline.com/{TENANT}/v2.0"
                    },
                }
            ]
        }
    },
)
class MessagingExtensionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.user = get_user_model().objects.create_user("agent")
        self.agent = Agent.objects.create(user=self.user)
        self.account = SocialAccount.objects.create(
            user=self.user,
            provider=PROVIDER,
            uid="oidc-subject",
            extra_data={"id_token": {"oid": OID, "tid": TENANT}},
        )
        self.company = Company.objects.create(name="Acme")
        self.contact = Contact.objects.create(
            name="Alice",
            email="alice@example.com",
            company=self.company,
        )
        Call.objects.create(
            agent=self.agent, contact=self.contact, summary="Private call"
        )
        self.client = Client(enforce_csrf_checks=True)
        metadata = SimpleNamespace(
            public_key=self.key.public_key(), endorsements=["msteams"]
        )
        patcher = patch(
            "botframework.connector.auth.jwt_token_extractor._OpenIdMetadata.get",
            new=AsyncMock(return_value=metadata),
        )
        self.metadata = patcher.start()
        self.addCleanup(patcher.stop)

    def token(self, **changes):
        claims = {
            "iss": AuthenticationConstants.TO_BOT_FROM_CHANNEL_TOKEN_ISSUER,
            "aud": APP_ID,
            "exp": int(time.time()) + 3600,
            "nbf": int(time.time()) - 1,
            "serviceurl": SERVICE_URL,
        }
        claims.update(changes)
        return jwt.encode(
            claims, self.key, algorithm="RS256", headers={"kid": "test-key"}
        )

    def activity(self, text="acm", **changes):
        data = {
            "type": "invoke",
            "name": "composeExtension/query",
            "channelId": "msteams",
            "serviceUrl": SERVICE_URL,
            "from": {"id": "teams-user", "aadObjectId": OID},
            "recipient": {"id": APP_ID},
            "conversation": {"id": "conversation"},
            "channelData": {"tenant": {"id": TENANT}},
            "value": {
                "commandId": COMMAND_ID,
                "parameters": [{"name": "searchText", "value": text}],
                "queryOptions": {"skip": 0, "count": PAGE_SIZE},
            },
        }
        data.update(changes)
        return data

    def post(self, activity=None, token=None):
        return self.client.post(
            ENDPOINT,
            json.dumps(activity or self.activity()),
            content_type="application/json",
            HTTP_AUTHORIZATION=" ".join(("Bearer", token or self.token())),
        )

    def select(self, pk=None):
        return self.post(
            self.activity(
                name="composeExtension/selectItem",
                value={"customer_id": self.company.pk if pk is None else pk},
            )
        )

    def test_search_returns_insertable_adaptive_card_and_preview(self):
        self.agent.companies.add(self.company)
        response = self.post()
        self.assertEqual(response.status_code, 200)
        result = response.json()["composeExtension"]
        self.assertEqual(result["type"], "result")
        self.assertEqual(result["attachmentLayout"], "list")
        (attachment,) = result["attachments"]
        self.assertEqual(
            attachment["contentType"], "application/vnd.microsoft.card.hero"
        )
        self.assertEqual(attachment["preview"]["content"]["title"], "Acme")
        tap = attachment["preview"]["content"]["tap"]
        self.assertEqual(
            tap, {"type": "invoke", "value": {"customer_id": self.company.pk}}
        )
        self.assertNotIn("alice@example", json.dumps(result))
        response = self.select(tap["value"]["customer_id"])
        self.assertEqual(response.status_code, 200)
        (attachment,) = response.json()["composeExtension"]["attachments"]
        self.assertEqual(
            attachment["contentType"], "application/vnd.microsoft.card.adaptive"
        )
        self.assertEqual(attachment["content"]["type"], "AdaptiveCard")
        content = json.dumps(attachment["content"])
        self.assertIn("Alice", content)
        self.assertIn("alice@example", content)
        self.assertNotIn("Private call", content)
        self.assertNotIn("fixture", content)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.agent.companies.count(), 1)

    def test_search_and_selection_only_include_linked_companies(self):
        linked = Company.objects.create(name="Linked customer")
        unlinked = Company.objects.create(name="Unlinked customer")
        other = Agent.objects.create(user=get_user_model().objects.create_user("other"))
        other.companies.add(unlinked)
        self.agent.companies.add(linked)

        response = self.post(self.activity(text="customer"))

        self.assertEqual(response.status_code, 200)
        titles = [
            attachment["preview"]["content"]["title"]
            for attachment in response.json()["composeExtension"]["attachments"]
        ]
        self.assertEqual(titles, ["Linked customer"])
        self.assertEqual(self.select(unlinked.pk).status_code, 404)

    def action(self, name, **value):
        value.setdefault("commandId", "logCallSummary")
        return self.post(self.activity(name=name, value=value))

    def test_action_command_flow_saves_summary_from_message(self):
        self.agent.companies.add(self.company)
        call = self.agent.calls.get()
        response = self.action(
            "composeExtension/fetchTask",
            commandContext="message",
            messagePayload={
                "body": {"contentType": "html", "content": "<p>Hello &amp; bye</p>"}
            },
        )
        self.assertEqual(response.status_code, 200)
        card = response.json()["task"]["value"]["card"]["content"]
        self.assertEqual(
            card["body"][1]["choices"],
            [{"title": "Acme", "value": str(self.company.pk)}],
        )
        data = card["actions"][0]["data"]
        self.assertEqual(data["message"], "Hello & bye")
        response = self.action(
            "composeExtension/submitAction",
            data={**data, "customer_id": str(self.company.pk)},
        )
        card = response.json()["task"]["value"]["card"]["content"]
        data = card["actions"][0]["data"]
        self.assertEqual(card["body"][1]["choices"][0]["value"], str(self.contact.pk))
        response = self.action(
            "composeExtension/submitAction",
            data={**data, "contact_id": str(self.contact.pk)},
        )
        card = response.json()["task"]["value"]["card"]["content"]
        self.assertEqual(card["body"][2]["value"], "Hello & bye")
        data = card["actions"][0]["data"]
        response = self.action(
            "composeExtension/submitAction",
            data={**data, "call_id": str(call.pk), "summary": "Done"},
        )
        self.assertEqual(response.json()["task"]["type"], "message")
        call.refresh_from_db()
        self.assertEqual(call.summary, "Private call\n\nDone")

    def test_action_command_appends_to_existing_summary(self):
        self.agent.companies.add(self.company)
        call = self.agent.calls.get()
        self.action(
            "composeExtension/submitAction",
            data={
                "step": "call",
                "contact_id": str(self.contact.pk),
                "call_id": str(call.pk),
                "summary": "New note",
            },
        )
        call.refresh_from_db()
        self.assertEqual(call.summary, "Private call\n\nNew note")

    def test_action_command_rejects_other_agents_calls(self):
        self.agent.companies.add(self.company)
        other = Agent.objects.create(user=get_user_model().objects.create_user("other"))
        call = Call.objects.create(agent=other, contact=self.contact)
        response = self.action(
            "composeExtension/submitAction",
            data={
                "step": "call",
                "contact_id": str(self.contact.pk),
                "call_id": str(call.pk),
                "summary": "x",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.action(
                "composeExtension/submitAction", data={"step": "x"}
            ).status_code,
            404,
        )

    def test_action_command_lists_only_linked_companies(self):
        linked = Company.objects.create(name="Linked customer")
        unlinked = Company.objects.create(name="Other customer")
        other = Agent.objects.create(user=get_user_model().objects.create_user("other"))
        other.companies.add(unlinked)
        self.agent.companies.add(self.company, linked)

        response = self.action("composeExtension/fetchTask")

        self.assertEqual(response.status_code, 200)
        card = response.json()["task"]["value"]["card"]["content"]
        self.assertEqual(
            card["body"][1]["choices"],
            [
                {"title": "Acme", "value": str(self.company.pk)},
                {"title": "Linked customer", "value": str(linked.pk)},
            ],
        )

    def test_action_command_without_linked_companies_has_no_customers(self):
        response = self.action("composeExtension/fetchTask")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["task"],
            {"type": "message", "value": "No customers are available."},
        )

    def test_action_command_lists_only_selected_linked_company_contacts(self):
        self.agent.companies.add(self.company)
        other = Company.objects.create(name="Other customer")
        Contact.objects.create(
            name="Other contact", email="other@example.com", company=other
        )

        response = self.action(
            "composeExtension/submitAction",
            data={"step": "customer", "customer_id": str(self.company.pk)},
        )

        self.assertEqual(response.status_code, 200)
        card = response.json()["task"]["value"]["card"]["content"]
        self.assertEqual(
            card["body"][1]["choices"],
            [{"title": "Alice (alice@example.com)", "value": str(self.contact.pk)}],
        )

    def test_action_command_rejects_unlinked_company_at_every_step(self):
        call = self.agent.calls.get()
        for step in ("customer", "contact", "call"):
            with self.subTest(step=step):
                response = self.action(
                    "composeExtension/submitAction",
                    data={
                        "step": step,
                        "customer_id": str(self.company.pk),
                        "contact_id": str(self.contact.pk),
                        "call_id": str(call.pk),
                        "summary": "Unauthorized note",
                    },
                )
                self.assertEqual(response.status_code, 404)
        call.refresh_from_db()
        self.assertEqual(call.summary, "Private call")

    def test_action_command_rechecks_company_membership_after_fetch(self):
        self.agent.companies.add(self.company)
        response = self.action("composeExtension/fetchTask")
        self.assertEqual(response.status_code, 200)
        self.agent.companies.remove(self.company)
        call = self.agent.calls.get()

        response = self.action(
            "composeExtension/submitAction",
            data={
                "step": "call",
                "contact_id": str(self.contact.pk),
                "call_id": str(call.pk),
                "summary": "Unauthorized note",
            },
        )

        self.assertEqual(response.status_code, 404)
        call.refresh_from_db()
        self.assertEqual(call.summary, "Private call")

    def test_empty_query_no_matches_and_customer_without_contacts(self):
        empty = Company.objects.create(name="Empty customer")
        self.agent.companies.add(self.company, empty)
        result = self.post(self.activity(text="missing")).json()["composeExtension"]
        self.assertEqual(result["attachments"], [])
        result = self.select(empty.pk).json()["composeExtension"]
        self.assertIn("No contacts recorded.", json.dumps(result))
        result = self.post(self.activity(text="  ")).json()["composeExtension"]
        self.assertEqual(len(result["attachments"]), 2)

    def test_results_are_ordered_paginated_and_bounded(self):
        companies = [
            Company.objects.create(name=f"Customer {index:02}") for index in range(30)
        ]
        self.agent.companies.add(*companies)
        activity = self.activity(text="Customer")
        activity["value"]["queryOptions"] = {"skip": 0, "count": PAGE_SIZE}
        attachments = self.post(activity).json()["composeExtension"]["attachments"]
        self.assertEqual(len(attachments), PAGE_SIZE)
        self.assertEqual(attachments[-1]["preview"]["content"]["title"], "Customer 24")
        activity["value"]["queryOptions"]["skip"] = PAGE_SIZE
        attachments = self.post(activity).json()["composeExtension"]["attachments"]
        self.assertEqual(len(attachments), 5)
        self.assertEqual(attachments[0]["preview"]["content"]["title"], "Customer 25")

    def test_contact_limit_and_markdown_escaping(self):
        self.agent.companies.add(self.company)
        self.company.name = "[Acme](https://untrusted.example)"
        self.company.save()
        for index in range(CONTACT_LIMIT + 1):
            Contact.objects.create(
                name=f"Contact {index:02}",
                email=f"contact{index}@example.com",
                company=self.company,
            )
        card = self.select().json()["composeExtension"]["attachments"][0]["content"]
        self.assertIn("\\[Acme\\]", card["body"][0]["text"])
        self.assertEqual(
            len([block for block in card["body"] if block.get("separator")]),
            CONTACT_LIMIT,
        )
        self.assertIn(
            f"Showing {CONTACT_LIMIT} of {CONTACT_LIMIT + 2}", card["body"][-1]["text"]
        )

    def test_card_byte_limit_with_unicode_contacts(self):
        self.agent.companies.add(self.company)
        Call.objects.filter(contact__company=self.company).delete()
        self.company.contacts.all().delete()
        for index in range(CONTACT_LIMIT):
            Contact.objects.create(
                name="界" * 195 + str(index),
                email="x" * 200 + "@example.com",
                company=self.company,
            )
        card = self.select().json()["composeExtension"]["attachments"][0]["content"]
        self.assertLessEqual(len(json.dumps(card).encode("utf-8")), CARD_BYTE_LIMIT)
        self.assertIn("Showing", card["body"][-1]["text"])

    def test_invalid_search_parameters(self):
        for value in (
            None,
            {},
            {"commandId": "wrong", "parameters": []},
            {
                "commandId": COMMAND_ID,
                "parameters": [{"name": "wrong", "value": "Acme"}],
            },
            {
                "commandId": COMMAND_ID,
                "parameters": [{"name": "searchText", "value": 123}],
            },
            {
                "commandId": COMMAND_ID,
                "parameters": [{"name": "searchText", "value": "x" * 201}],
            },
        ):
            with self.subTest(value=value):
                self.assertEqual(self.post(self.activity(value=value)).status_code, 400)
        for options in (
            "invalid",
            {"skip": -1},
            {"skip": True},
            {"skip": 10001},
            {"count": 0},
            {"count": "5"},
            {"count": PAGE_SIZE + 1},
        ):
            activity = self.activity()
            activity["value"]["queryOptions"] = options
            with self.subTest(options=options):
                self.assertEqual(self.post(activity).status_code, 400)

    def test_authorization_is_required_even_with_browser_session(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post(
                ENDPOINT,
                json.dumps(self.activity()),
                content_type="application/json",
            ).status_code,
            401,
        )
        self.assertEqual(self.post(token="not-a-jwt").status_code, 401)
        self.assertEqual(
            self.post(token=self.token(aud="another-app")).status_code, 401
        )
        self.assertEqual(
            self.post(
                token=self.token(serviceurl="https://another.example/")
            ).status_code,
            401,
        )
        self.assertEqual(
            self.post(token=self.token(exp=int(time.time()) - 3600)).status_code, 401
        )
        self.assertEqual(
            self.post(token=self.token(iss="https://untrusted.example")).status_code,
            401,
        )
        self.metadata.side_effect = IndexError("Unknown signing key")
        self.assertEqual(self.post().status_code, 401)

    def test_invalid_signature_is_rejected(self):
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        token = jwt.encode(
            claims, wrong_key, algorithm="RS256", headers={"kid": "test-key"}
        )
        self.assertEqual(self.post(token=token).status_code, 401)

    def test_unlinked_inactive_and_unprovisioned_users_are_denied(self):
        activity = self.activity()
        activity["from"]["aadObjectId"] = "44444444-4444-4444-4444-444444444444"
        self.assertEqual(self.post(activity).status_code, 403)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.post().status_code, 403)
        self.user.is_active = True
        self.user.save()
        self.agent.delete()
        self.assertEqual(self.post().status_code, 403)

    def test_duplicate_linked_identity_is_denied(self):
        SocialAccount.objects.create(
            user=get_user_model().objects.create_user("peer"),
            provider=PROVIDER,
            uid="other-subject",
            extra_data=self.account.extra_data,
        )
        self.assertEqual(self.post().status_code, 403)

    def test_cross_tenant_and_missing_identity_are_denied(self):
        self.assertEqual(
            self.post(self.activity(channelData={"tenant": None})).status_code, 400
        )
        for channel_data in (
            {},
            {"tenant": {"id": "invalid"}},
            {"tenant": {"id": "44444444-4444-4444-4444-444444444444"}},
        ):
            with self.subTest(channel_data=channel_data):
                self.assertEqual(
                    self.post(self.activity(channelData=channel_data)).status_code, 403
                )
        self.assertEqual(
            self.post(self.activity(**{"from": {"id": "user"}})).status_code, 403
        )
        with override_settings(TEAMS_BOT_PROVIDER_ID="missing"):
            self.assertEqual(self.post().status_code, 403)

    def test_other_channels_and_unsupported_invokes_are_rejected(self):
        self.assertEqual(
            self.post(self.activity(channelId="emulator")).status_code, 400
        )
        self.assertEqual(
            self.post(
                self.activity(name="composeExtension/anonymousQueryLink")
            ).status_code,
            501,
        )

    @patch(
        "crm.teams.messaging_extension.bot.TeamsChannelAdapter.send_activities",
        new_callable=AsyncMock,
    )
    def test_message_receives_conversation_disabled_reply(self, send):
        send.return_value = [ResourceResponse(id="reply")]
        for text in ("Hello", None):
            with self.subTest(text=text):
                activity = self.activity(
                    type="message", id="incoming-message", text=text
                )
                del activity["name"]
                del activity["value"]
                response = self.post(activity)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"")
                self.assertEqual(response["Cache-Control"], "private, no-store")
                send.assert_awaited_once()
                context, activities = send.call_args.args
                (reply,) = activities
                self.assertEqual(reply.type, "message")
                self.assertEqual(
                    reply.text, "This bot is not enabled for conversation."
                )
                self.assertEqual(reply.conversation.id, "conversation")
                self.assertEqual(reply.recipient.id, "teams-user")
                self.assertEqual(reply.reply_to_id, "incoming-message")
                send.reset_mock()

    @patch(
        "crm.teams.messaging_extension.bot.TeamsChannelAdapter.send_activities",
        new_callable=AsyncMock,
    )
    def test_message_notice_does_not_require_crm_provisioning(self, send):
        send.return_value = [ResourceResponse(id="reply")]
        self.account.delete()
        self.agent.delete()
        response = self.post(self.activity(type="message", value=None, text="Hello"))
        self.assertEqual(response.status_code, 200)
        send.assert_awaited_once()

    @patch(
        "crm.teams.messaging_extension.bot.TeamsChannelAdapter.send_activities",
        new_callable=AsyncMock,
    )
    def test_messages_still_require_channel_authentication_and_validation(self, send):
        activity = self.activity(type="message", value=None, text="Hello")
        for token in (
            "not-a-jwt",
            self.token(aud="another-app"),
            self.token(serviceurl="https://another.example/"),
        ):
            with self.subTest(token=token):
                self.assertEqual(self.post(activity, token=token).status_code, 401)
        self.assertEqual(
            self.client.post(
                ENDPOINT,
                json.dumps(activity),
                content_type="application/json",
            ).status_code,
            401,
        )
        activity["channelId"] = "emulator"
        self.assertEqual(self.post(activity).status_code, 400)
        activity["channelId"] = "msteams"
        activity["type"] = "event"
        self.assertEqual(self.post(activity).status_code, 400)
        send.assert_not_awaited()

    def test_selection_rechecks_authorization_and_loads_current_details(self):
        self.agent.companies.add(self.company)
        self.contact.name = "Updated contact"
        self.contact.save()
        self.assertIn("Updated contact", json.dumps(self.select().json()))
        self.assertEqual(self.select(999999).status_code, 404)
        for pk in ("1", True, -1, 2**63):
            with self.subTest(pk=pk):
                self.assertEqual(self.select(pk).status_code, 400)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.select().status_code, 403)

    def test_search_payload_is_bounded_for_long_unicode_names(self):
        companies = [
            Company.objects.create(name="😀" * 190 + str(index))
            for index in range(PAGE_SIZE)
        ]
        self.agent.companies.add(*companies)
        response = self.post(self.activity(text="😀"))
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.content), 100000)

    def test_disabled_and_http_input_validation(self):
        with override_settings(TEAMS_BOT_APP_PASSWORD=""):
            self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.client.get(ENDPOINT).status_code, 405)
        self.assertEqual(
            self.post(self.activity(**{"from": "invalid"})).status_code, 400
        )
        for body in ("not-json", "[]"):
            self.assertEqual(
                self.client.post(
                    ENDPOINT,
                    body,
                    content_type="application/json",
                    HTTP_AUTHORIZATION=" ".join(("Bearer", "fixture")),
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                ENDPOINT,
                "{}",
                content_type="text/plain",
                HTTP_AUTHORIZATION=" ".join(("Bearer", "fixture")),
            ).status_code,
            415,
        )
        self.assertEqual(
            self.client.post(
                ENDPOINT,
                "x" * 65537,
                content_type="application/json",
                HTTP_AUTHORIZATION=" ".join(("Bearer", "fixture")),
            ).status_code,
            413,
        )


class MessagingExtensionManifestTests(SimpleTestCase):
    def manifest(self):
        return json.loads(
            (
                Path(settings.BASE_DIR)
                / "teams"
                / "tabs-bot-extension"
                / "manifest.json"
            ).read_text()
        )

    def test_manifest_matches_handler(self):
        manifest = self.manifest()
        (extension,) = manifest["composeExtensions"]
        command, action = extension["commands"]
        self.assertEqual(action["id"], "logCallSummary")
        self.assertEqual(action["type"], "action")
        self.assertTrue(action["fetchTask"])
        self.assertIn("message", action["context"])
        self.assertEqual(command["id"], COMMAND_ID)
        self.assertEqual(command["type"], "query")
        self.assertIn("compose", command["context"])
        self.assertEqual(command["parameters"][0]["name"], "searchText")

    def test_manifest_icons_are_valid_teams_pngs(self):
        icons = self.manifest()["icons"]
        for kind, size in (("color", (192, 192)), ("outline", (32, 32))):
            with self.subTest(kind=kind):
                path = (
                    Path(settings.BASE_DIR)
                    / "teams"
                    / "tabs-bot-extension"
                    / icons[kind]
                )
                with Image.open(path) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, size)
                    image.load()
                    if kind == "outline":
                        self.assertEqual(image.mode, "RGBA")
                        self.assertEqual(image.getchannel("A").getextrema(), (0, 255))
                        self.assertTrue(
                            all(
                                (red, green, blue) == (255, 255, 255)
                                for red, green, blue, alpha in image.getdata()
                                if alpha
                            )
                        )
                    else:
                        self.assertEqual(
                            image.convert("RGBA").getchannel("A").getextrema(),
                            (255, 255),
                        )

    def test_manifest_includes_existing_tab_routes(self):
        manifest = self.manifest()
        (personal,) = manifest["staticTabs"]
        self.assertEqual(personal["scopes"], ["personal"])
        self.assertEqual(
            personal["contentUrl"],
            "https://<PORTAL_HOST>/teams/tab/?provider=<TEAMS_OIDC_PROVIDER_ID>",
        )
        self.assertEqual(personal["websiteUrl"], "https://<PORTAL_HOST>/")
        (configurable,) = manifest["configurableTabs"]
        self.assertEqual(
            configurable["configurationUrl"], "https://<PORTAL_HOST>/teams/config/"
        )
        self.assertEqual(configurable["scopes"], ["team", "groupChat"])
        self.assertTrue(configurable["canUpdateConfiguration"])

    def test_manifest_sso_uses_portal_application_not_bot_application(self):
        manifest = self.manifest()
        self.assertEqual(
            manifest["webApplicationInfo"],
            {
                "id": "<TEAMS_SSO_APP_ID>",
                "resource": "api://<PORTAL_HOST>/<TEAMS_SSO_APP_ID>",
            },
        )
        self.assertEqual(manifest["id"], "<TEAMS_APP_ID>")
        self.assertEqual(
            manifest["composeExtensions"][0]["botId"], "<TEAMS_BOT_APP_ID>"
        )
        self.assertEqual(manifest["validDomains"], ["<PORTAL_HOST>"])
