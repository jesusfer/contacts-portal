import json
import io
import importlib
import os
import secrets
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from config.settings import configure_oidc
from ..adapters import AccountAdapter, SocialAccountAdapter
from ..forms import AppointmentForm, CallForm
from ..models import Agent, Appointment, Call, Company, Contact, Email


@override_settings(SECURE_SSL_REDIRECT=False)
class APITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="First")
        cls.other_company = Company.objects.create(name="Second")
        cls.user = get_user_model().objects.create_user("agent")
        cls.user.set_password("test-passphrase-123")
        cls.user.save()
        cls.peer = get_user_model().objects.create_user("peer")
        cls.other_user = get_user_model().objects.create_user("other")
        cls.unprovisioned = get_user_model().objects.create_user("waiting")
        cls.agent = Agent.objects.create(user=cls.user)
        cls.peer_agent = Agent.objects.create(user=cls.peer)
        cls.other_agent = Agent.objects.create(user=cls.other_user)
        cls.agent.companies.add(cls.company)
        cls.peer_agent.companies.add(cls.company)
        cls.other_agent.companies.add(cls.other_company)
        cls.contact = Contact.objects.create(
            name="Alice", email="alice@example.com", company=cls.company
        )
        cls.other_contact = Contact.objects.create(
            name="Bob", email="bob@example.com", company=cls.other_company
        )
        cls.call = Call.objects.create(agent=cls.agent, contact=cls.contact)
        cls.peer_call = Call.objects.create(agent=cls.peer_agent, contact=cls.contact)
        cls.other_call = Call.objects.create(
            agent=cls.other_agent, contact=cls.other_contact
        )
        cls.start = timezone.now() + timedelta(days=1)
        cls.appointment = Appointment.objects.create(
            call=cls.call, starts_at=cls.start, ends_at=cls.start + timedelta(hours=1)
        )
        cls.peer_appointment = Appointment.objects.create(
            call=cls.peer_call,
            starts_at=cls.start,
            ends_at=cls.start + timedelta(hours=1),
        )
        cls.other_appointment = Appointment.objects.create(
            call=cls.other_call,
            starts_at=cls.start,
            ends_at=cls.start + timedelta(hours=1),
        )
        cls.email = Email.objects.create(
            agent=cls.agent, contact=cls.contact, subject="Hello"
        )
        cls.peer_email = Email.objects.create(
            agent=cls.peer_agent, contact=cls.contact, subject="Private"
        )
        cls.other_email = Email.objects.create(
            agent=cls.other_agent, contact=cls.other_contact, subject="Other"
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def send(self, method, path, data, *, client=None, **extra):
        return getattr(client or self.client, method)(
            path, data=json.dumps(data), content_type="application/json", **extra
        )

    def test_session_anonymous_and_provisioning(self):
        anonymous = Client()
        response = anonymous.get("/api/session/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["user"])
        self.assertIn("csrftoken", response.cookies)
        response = self.client.get("/api/session/").json()
        self.assertEqual(
            response["agent"]["companies"], [{"id": self.company.id, "name": "First"}]
        )
        self.assertEqual(response["user"]["username"], "agent")
        self.client.force_login(self.unprovisioned)
        self.assertIsNone(self.client.get("/api/session/").json()["agent"])
        for path in ("companies", "contacts", "calls", "appointments", "inbox", "kpis"):
            self.assertEqual(self.client.get(f"/api/{path}/").status_code, 403)

    def test_unauthenticated_apis_and_methods(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/calls/").status_code, 401)
        self.assertEqual(self.client.get("/api/login/").status_code, 405)
        self.assertEqual(self.client.delete("/api/session/").status_code, 405)
        self.assertEqual(self.client.get("/api/not-real/").status_code, 404)
        self.assertEqual(self.client.get("/accounts/not-real/").status_code, 404)
        self.assertEqual(self.client.get("/admin/not-real/").status_code, 302)

    def test_csrf_login_mutations_and_logout(self):
        client = Client(enforce_csrf_checks=True)
        credentials = {"username": "agent", "password": "test-passphrase-123"}
        self.assertEqual(
            self.send("post", "/api/login/", credentials, client=client).status_code,
            403,
        )
        client.get("/api/session/")
        response = self.send(
            "post",
            "/api/login/",
            credentials,
            client=client,
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], self.user.id)
        self.assertEqual(
            self.send("post", "/api/contacts/", {}, client=client).status_code, 403
        )
        self.assertEqual(
            self.send(
                "patch", f"/api/calls/{self.call.id}/", {}, client=client
            ).status_code,
            403,
        )
        self.assertEqual(client.post("/api/logout/").status_code, 403)
        response = client.post(
            "/api/logout/", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["user"])

    def test_invalid_login_and_rate_limit(self):
        self.client.logout()
        for _ in range(5):
            response = self.send(
                "post", "/api/login/", {"username": "agent", "password": "wrong"}
            )
            self.assertEqual(response.status_code, 401)
        response = self.send(
            "post",
            "/api/login/",
            {"username": "agent", "password": "test-passphrase-123"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(
            self.send(
                "post",
                "/api/login/",
                {"username": "agent", "password": "test-passphrase-123"},
            ).status_code,
            401,
        )

    def test_linked_contact_directory_and_creation_for_selected_company(self):
        result = self.client.get("/api/contacts/").json()["contacts"]
        self.assertEqual(
            [item["id"] for item in result], [self.contact.id]
        )
        response = self.send(
            "post",
            "/api/contacts/",
            {
                "name": "New",
                "email": "new@example.com",
                "company_id": self.company.id,
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["company"]["id"], self.company.id)
        self.assertEqual(
            self.send(
                "post",
                "/api/contacts/",
                {
                    "name": "Forbidden",
                    "email": "forbidden@example.com",
                    "company_id": self.other_company.id,
                },
            ).status_code,
            404,
        )
        self.assertFalse(Contact.objects.filter(name="Forbidden").exists())
        self.assertEqual(
            self.send(
                "post",
                "/api/contacts/",
                {"name": "Missing company", "email": "missing@example.com"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/contacts/",
                {"name": "New", "email": "new@example.com", "company_id": 999999},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/contacts/",
                {"name": "New", "email": "bad", "company_id": self.company.id},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/contacts/",
                {
                    "name": "New",
                    "email": "new@example.com",
                    "company_id": self.company.id,
                    "agent_id": self.peer_agent.id,
                },
            ).status_code,
            400,
        )

    def test_linked_company_directory_and_creation(self):
        result = self.client.get("/api/companies/").json()["companies"]
        self.assertEqual(
            result,
            [
                {"id": self.company.id, "name": "First"},
            ],
        )
        response = self.send("post", "/api/companies/", {"name": "Third"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "Third")
        self.agent.refresh_from_db()
        self.assertEqual(
            list(self.agent.companies.values_list("id", flat=True)),
            [self.company.id, response.json()["id"]],
        )
        self.assertEqual(len(self.client.get("/api/companies/").json()["companies"]), 2)
        self.assertEqual(
            self.client.get("/api/session/").json()["agent"]["companies"],
            self.client.get("/api/companies/").json()["companies"],
        )
        self.assertFalse(
            self.peer_agent.companies.filter(pk=response.json()["id"]).exists()
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/contacts/",
                {
                    "name": "New customer contact",
                    "email": "new@example.com",
                    "company_id": response.json()["id"],
                },
            ).status_code,
            201,
        )
        self.assertEqual(
            self.send("post", "/api/companies/", {"name": ""}).status_code, 400
        )
        self.assertEqual(
            self.send(
                "post", "/api/companies/", {"name": "New", "agent_id": self.agent.id}
            ).status_code,
            400,
        )
        self.client.logout()
        self.assertEqual(self.client.get("/api/companies/").status_code, 401)
        self.assertEqual(
            self.send("post", "/api/companies/", {"name": "New"}).status_code, 401
        )

    def test_all_list_endpoints_exclude_other_agents(self):
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/calls/").json()["calls"]],
            [self.call.id],
        )
        self.assertEqual(
            [
                item["id"]
                for item in self.client.get("/api/appointments/").json()["appointments"]
            ],
            [self.appointment.id],
        )
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/inbox/").json()["emails"]],
            [self.email.id],
        )

    def test_detail_ownership_404_even_same_company(self):
        for path in (
            f"/api/calls/{self.peer_call.id}/",
            f"/api/calls/{self.other_call.id}/",
            f"/api/appointments/{self.peer_appointment.id}/",
            f"/api/appointments/{self.other_appointment.id}/",
        ):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.send("patch", path, {}).status_code, 404)
        for email in (self.peer_email, self.other_email):
            self.assertEqual(
                self.send("patch", f"/api/inbox/{email.id}/", {}).status_code, 404
            )

    def test_unlinked_contacts_rejected_and_activity_relations_owned(self):
        response = self.send(
            "post", "/api/calls/", {"contact_id": self.other_contact.id}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Call.objects.filter(agent=self.agent, contact=self.other_contact).exists()
        )
        response = self.send(
            "post",
            "/api/inbox/",
            {"contact_id": self.other_contact.id, "subject": "Bad"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Email.objects.filter(agent=self.agent, contact=self.other_contact).exists()
        )
        response = self.send("post", "/api/calls/", {"contact_id": self.contact.id})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["contact"]["company"]["id"], self.company.id
        )
        self.assertEqual(
            Call.objects.get(pk=response.json()["id"]).agent_id, self.agent.id
        )
        own_call = response.json()["id"]
        self.assertEqual(self.client.get(f"/api/calls/{own_call}/").status_code, 200)
        response = self.send(
            "post",
            "/api/inbox/",
            {"contact_id": self.contact.id, "subject": "Allowed"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Email.objects.get(pk=response.json()["id"]).agent_id, self.agent.id
        )
        imported_email = response.json()["id"]
        self.assertEqual(
            self.send(
                "patch", f"/api/inbox/{imported_email}/", {"is_read": True}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/appointments/",
                {
                    "call_id": own_call,
                    "starts_at": self.start.isoformat(),
                    "ends_at": (self.start + timedelta(hours=1)).isoformat(),
                },
            ).status_code,
            201,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/appointments/",
                {
                    "call_id": self.peer_call.id,
                    "starts_at": self.start.isoformat(),
                    "ends_at": (self.start + timedelta(hours=1)).isoformat(),
                },
            ).status_code,
            404,
        )
        self.assertEqual(
            self.send("post", "/api/calls/", {"contact_id": 999999}).status_code, 404
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/calls/",
                {"contact_id": self.other_contact.id, "agent_id": self.other_agent.id},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/inbox/",
                {
                    "contact_id": self.other_contact.id,
                    "subject": "Spoofed",
                    "agent_id": self.other_agent.id,
                },
            ).status_code,
            400,
        )

    def test_multiple_company_links_allow_contacts_and_owned_activity(self):
        self.agent.companies.add(self.other_company)
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/contacts/").json()["contacts"]],
            [self.contact.id, self.other_contact.id],
        )
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/companies/").json()["companies"]],
            [self.company.id, self.other_company.id],
        )
        response = self.send(
            "post",
            "/api/calls/",
            {"contact_id": self.other_contact.id, "status": "completed", "rating": 5},
        )
        self.assertEqual(response.status_code, 201)
        call_id = response.json()["id"]
        self.assertEqual(self.client.get(f"/api/calls/{call_id}/").status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/calls/{self.other_call.id}/").status_code, 404
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/inbox/",
                {"contact_id": self.other_contact.id, "subject": "Allowed"},
            ).status_code,
            201,
        )
        kpis = self.client.get("/api/kpis/").json()
        self.assertEqual(kpis["total_calls"], 2)
        self.assertEqual(
            [row["id"] for row in kpis["by_contact"]],
            [self.contact.id, self.other_contact.id],
        )

    def test_removed_company_link_hides_activity_and_blocks_mutations(self):
        self.agent.companies.add(self.other_company)
        hidden_call = Call.objects.create(
            agent=self.agent, contact=self.other_contact, status="completed", rating=5
        )
        hidden_appointment = Appointment.objects.create(
            call=hidden_call, starts_at=self.start, ends_at=self.start + timedelta(hours=1)
        )
        hidden_email = Email.objects.create(
            agent=self.agent, contact=self.other_contact, subject="Hidden"
        )
        self.agent.companies.remove(self.other_company)

        for endpoint, key, expected_id in (
            ("calls", "calls", self.call.id),
            ("appointments", "appointments", self.appointment.id),
            ("inbox", "emails", self.email.id),
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    [item["id"] for item in self.client.get(f"/api/{endpoint}/").json()[key]],
                    [expected_id],
                )
        kpis = self.client.get("/api/kpis/").json()
        self.assertEqual(kpis["total_calls"], 1)
        self.assertEqual(kpis["completed_calls"], 0)
        self.assertIsNone(kpis["average_rating"])
        self.assertEqual([row["id"] for row in kpis["by_contact"]], [self.contact.id])
        self.assertEqual([row["id"] for row in kpis["by_company"]], [self.company.id])
        for path, changes in (
            (f"/api/calls/{hidden_call.id}/", {"summary": "No"}),
            (f"/api/appointments/{hidden_appointment.id}/", {"notes": "No"}),
        ):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.send("patch", path, changes).status_code, 404)
        self.assertEqual(
            self.send(
                "patch", f"/api/inbox/{hidden_email.id}/", {"is_read": True}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/appointments/",
                {
                    "call_id": hidden_call.id,
                    "starts_at": self.start.isoformat(),
                    "ends_at": (self.start + timedelta(hours=1)).isoformat(),
                },
            ).status_code,
            404,
        )
        hidden_call.refresh_from_db()
        hidden_appointment.refresh_from_db()
        hidden_email.refresh_from_db()
        self.assertEqual(hidden_call.summary, "")
        self.assertEqual(hidden_appointment.notes, "")
        self.assertFalse(hidden_email.is_read)

    def test_agent_without_company_links_has_empty_workspace(self):
        self.agent.companies.clear()
        for endpoint, key in (
            ("companies", "companies"),
            ("contacts", "contacts"),
            ("calls", "calls"),
            ("appointments", "appointments"),
            ("inbox", "emails"),
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(f"/api/{endpoint}/").json()[key], [])
        kpis = self.client.get("/api/kpis/").json()
        self.assertEqual(kpis["total_calls"], 0)
        self.assertEqual(kpis["completed_calls"], 0)
        self.assertIsNone(kpis["average_rating"])
        self.assertEqual(kpis["missed_percentage"], 0)
        self.assertEqual(kpis["rescheduled_percentage"], 0)
        self.assertEqual(kpis["by_contact"], [])
        self.assertEqual(kpis["by_company"], [])
        self.assertEqual(
            self.send("post", "/api/calls/", {"contact_id": self.contact.id}).status_code,
            404,
        )

    def test_call_creation_and_patch(self):
        response = self.send(
            "post", "/api/calls/", {"contact_id": self.contact.id, "summary": "Planned"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "scheduled")
        self.assertFalse(response.json()["was_rescheduled"])
        self.assertIsNone(response.json()["appointment"])
        self.assertEqual(Call.objects.get(pk=response.json()["id"]).agent, self.agent)
        response = self.send(
            "patch",
            f"/api/calls/{self.call.id}/",
            {"status": "completed", "rating": 4, "summary": "Done"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rating"], 4)
        self.assertEqual(response.json()["appointment"]["id"], self.appointment.id)
        response = self.send(
            "patch", f"/api/calls/{self.call.id}/", {"status": "missed"}
        )
        self.assertEqual(response.status_code, 400)
        self.call.refresh_from_db()
        self.assertEqual(self.call.status, "completed")
        self.assertEqual(self.call.rating, 4)
        response = self.send(
            "patch", f"/api/calls/{self.call.id}/", {"status": "missed", "rating": None}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["rating"])
        self.assertEqual(
            self.send(
                "patch", f"/api/calls/{self.call.id}/", {"agent_id": self.peer_agent.id}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.send(
                "patch",
                f"/api/calls/{self.call.id}/",
                {"contact_id": self.other_contact.id},
            ).status_code,
            400,
        )

    def test_invalid_ratings_and_status(self):
        for rating in (0, 6, 1.5, "3", True):
            response = self.send(
                "post",
                "/api/calls/",
                {
                    "contact_id": self.contact.id,
                    "status": "completed",
                    "rating": rating,
                },
            )
            self.assertEqual(response.status_code, 400, response.content)
        for status in ("scheduled", "missed", "rescheduled", "cancelled", "unknown"):
            response = self.send(
                "patch", f"/api/calls/{self.call.id}/", {"status": status, "rating": 3}
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.send(
                "patch",
                f"/api/calls/{self.call.id}/",
                {"status": "completed", "rating": None},
            ).status_code,
            200,
        )

    def test_appointment_creation_and_duplicate(self):
        call = Call.objects.create(agent=self.agent, contact=self.contact)
        data = {
            "call_id": call.id,
            "starts_at": self.start.isoformat(),
            "ends_at": (self.start + timedelta(hours=1)).isoformat(),
            "notes": "Meeting",
        }
        response = self.send("post", "/api/appointments/", data)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["call"]["id"], call.id)
        self.assertEqual(
            response.json()["call"]["contact"]["company"]["id"], self.company.id
        )
        self.assertEqual(self.send("post", "/api/appointments/", data).status_code, 400)

    def test_schedule_validation(self):
        for data in (
            {"ends_at": self.start.isoformat()},
            {"ends_at": (self.start - timedelta(seconds=1)).isoformat()},
            {"starts_at": "not-a-date"},
            {"starts_at": "2026-10-10T12:00:00"},
            {"starts_at": "2026-10-10"},
            {"starts_at": None},
        ):
            response = self.send(
                "patch", f"/api/appointments/{self.appointment.id}/", data
            )
            self.assertEqual(response.status_code, 400, response.content)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.starts_at, self.start)
        self.call.refresh_from_db()
        self.assertEqual(self.call.status, "scheduled")

    def test_schedule_changes_reschedule_pending_not_terminal(self):
        path = f"/api/appointments/{self.appointment.id}/"
        response = self.send("patch", path, {"notes": "Only notes"})
        self.assertEqual(response.json()["call"]["status"], "scheduled")
        response = self.send(
            "patch", path, {"ends_at": (self.start + timedelta(hours=2)).isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["call"]["status"], "rescheduled")
        self.assertTrue(response.json()["call"]["was_rescheduled"])
        self.call.refresh_from_db()
        self.assertIsNone(self.call.rating)
        self.assertTrue(self.call.was_rescheduled)
        self.call.status = "completed"
        self.call.rating = 5
        self.call.save()
        response = self.send(
            "patch", path, {"ends_at": (self.start + timedelta(hours=3)).isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["call"]["status"], "completed")
        response = self.send("patch", path, {"notes": "Corrected historical notes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["notes"], "Corrected historical notes")
        self.assertEqual(response.json()["call"]["status"], "completed")
        self.assertEqual(self.send("patch", path, {}).status_code, 200)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.ends_at, self.start + timedelta(hours=3))
        self.call.refresh_from_db()
        self.assertEqual(self.call.rating, 5)
        for status in ("missed", "cancelled"):
            self.call.status, self.call.rating = status, None
            self.call.save()
            response = self.send("patch", path, {"notes": status})
            self.assertEqual(response.json()["call"]["status"], status)

    def test_inbox_import_and_tracking(self):
        response = self.send(
            "post",
            "/api/inbox/",
            {"contact_id": self.contact.id, "subject": "Imported", "body": "Body"},
        )
        self.assertEqual(response.status_code, 201, response.content)
        email_id = response.json()["id"]
        self.assertEqual(Email.objects.get(pk=email_id).agent, self.agent)
        response = self.send(
            "patch", f"/api/inbox/{email_id}/", {"is_read": True, "is_replied": True}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["is_replied"])
        self.assertEqual(
            self.send(
                "patch", f"/api/inbox/{email_id}/", {"is_read": "true"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.send(
                "patch", f"/api/inbox/{email_id}/", {"body": "Changed"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.send(
                "patch", f"/api/inbox/{email_id}/", {"agent_id": self.peer_agent.id}
            ).status_code,
            400,
        )

    def test_malformed_payloads(self):
        for body in (
            "{",
            "[]",
            "null",
            '"string"',
            '{"status": NaN}',
            '{"summary": 3}',
        ):
            response = self.client.patch(
                f"/api/calls/{self.call.id}/",
                data=body,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400, response.content)
        response = self.client.patch(
            f"/api/calls/{self.call.id}/", data=b"\xff", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/contacts/", {"name": "Wrong content type"}
            ).status_code,
            400,
        )
        for data in (
            {},
            {"contact_id": True},
            {"contact_id": -1},
            {"contact_id": None},
        ):
            self.assertEqual(self.send("post", "/api/calls/", data).status_code, 400)

    def test_kpis_completed_only_and_non_scheduled_denominator(self):
        self.agent.companies.add(self.other_company)
        for status, rating in (
            ("completed", 5),
            ("completed", 3),
            ("completed", None),
            ("missed", None),
            ("rescheduled", None),
            ("cancelled", None),
        ):
            Call.objects.create(
                agent=self.agent, contact=self.contact, status=status, rating=rating
            )
        Call.objects.create(
            agent=self.agent, contact=self.other_contact, status="completed", rating=1
        )
        Call.objects.create(
            agent=self.peer_agent, contact=self.contact, status="completed", rating=1
        )
        result = self.client.get("/api/kpis/").json()
        self.assertEqual(result["total_calls"], 8)
        self.assertEqual(result["completed_calls"], 4)
        self.assertEqual(result["average_rating"], 3)
        self.assertAlmostEqual(result["missed_percentage"], 100 / 7)
        self.assertAlmostEqual(result["rescheduled_percentage"], 100 / 7)
        for dimension, identifier in (
            ("by_contact", self.contact.id),
            ("by_company", self.company.id),
        ):
            self.assertEqual(len(result[dimension]), 2)
            self.assertEqual(result[dimension][0]["id"], identifier)
            self.assertEqual(result[dimension][0]["average_rating"], 4)
            self.assertEqual(result[dimension][0]["total_calls"], 7)
            self.assertEqual(result[dimension][1]["average_rating"], 1)
            self.assertEqual(result[dimension][1]["total_calls"], 1)
            self.assertEqual(result[dimension][1]["completed_calls"], 1)
        self.assertEqual(result["by_company"][1]["id"], self.other_company.id)
        self.assertEqual(result["by_company"][1]["name"], "Second")

    def test_empty_kpis(self):
        Call.objects.filter(agent=self.agent).delete()
        result = self.client.get("/api/kpis/").json()
        self.assertEqual(result["total_calls"], 0)
        self.assertIsNone(result["average_rating"])
        self.assertEqual(result["missed_percentage"], 0)
        self.assertEqual(result["rescheduled_percentage"], 0)
        self.assertEqual(result["by_contact"], [])

    def test_reschedule_history_survives_completion_and_is_api_read_only(self):
        path = f"/api/calls/{self.call.id}/"
        response = self.send("patch", path, {"status": "rescheduled"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["was_rescheduled"])
        response = self.send("patch", path, {"status": "completed", "rating": 5})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["was_rescheduled"])
        self.assertEqual(
            self.client.get("/api/kpis/").json()["rescheduled_percentage"], 100
        )
        self.assertEqual(
            self.send("patch", path, {"was_rescheduled": False}).status_code, 400
        )
        self.assertEqual(
            self.send(
                "post",
                "/api/calls/",
                {"contact_id": self.contact.id, "was_rescheduled": True},
            ).status_code,
            400,
        )
        self.call.refresh_from_db()
        self.call.was_rescheduled = False
        self.call.summary = "History cannot be reset"
        self.call.save(update_fields=["summary"])
        self.call.refresh_from_db()
        self.assertTrue(self.call.was_rescheduled)

    def test_current_rescheduled_fallback_and_existing_history_backfill(self):
        Call.objects.filter(pk=self.call.id).update(
            status="rescheduled", was_rescheduled=False
        )
        self.assertEqual(
            self.client.get("/api/kpis/").json()["rescheduled_percentage"], 100
        )
        migration = importlib.import_module("crm.migrations.0002_call_was_rescheduled")
        migration.preserve_existing_reschedules(apps, connection.schema_editor())
        self.call.refresh_from_db()
        self.assertTrue(self.call.was_rescheduled)

    def test_rescheduled_reset_to_scheduled_excluded_until_completed(self):
        path = f"/api/calls/{self.call.id}/"
        self.assertEqual(
            self.send("patch", path, {"status": "rescheduled"}).status_code, 200
        )
        response = self.send("patch", path, {"status": "scheduled"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["was_rescheduled"])
        Call.objects.create(agent=self.agent, contact=self.contact, status="missed")
        result = self.client.get("/api/kpis/").json()
        self.assertEqual(result["total_calls"], 2)
        self.assertEqual(result["missed_percentage"], 100)
        self.assertEqual(result["rescheduled_percentage"], 0)
        self.assertEqual(
            self.send("patch", path, {"status": "completed", "rating": 4}).status_code,
            200,
        )
        result = self.client.get("/api/kpis/").json()
        self.assertEqual(result["rescheduled_percentage"], 50)
        self.assertEqual(result["missed_percentage"], 50)
        for metric in ("missed_percentage", "rescheduled_percentage"):
            self.assertGreaterEqual(result[metric], 0)
            self.assertLessEqual(result[metric], 100)

    def test_spa_missing_build_and_reserved_routes(self):
        with override_settings(FRONTEND_DIST=Path("crm/nonexistent-frontend")):
            self.assertEqual(self.client.get("/dashboard").status_code, 503)
            self.assertEqual(self.client.get("/api/unknown/").status_code, 404)
            self.assertEqual(self.client.get("/static/unknown.js").status_code, 404)

    def test_spa_serves_built_html_and_preserves_static_asset_urls(self):
        html = (
            b'<html><script type="module" src="/static/assets/app.js"></script></html>'
        )
        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.open", return_value=io.BytesIO(html)),
        ):
            response = self.client.get("/dashboard")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), html)
            self.assertEqual(response["Content-Type"], "text/html")
        from django.conf import settings

        self.assertEqual(settings.STATIC_URL, "/static/")


class ModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Company")
        cls.other_company = Company.objects.create(name="Other")
        cls.agent = Agent.objects.create(
            user=get_user_model().objects.create_user("agent")
        )
        cls.agent.companies.add(cls.company)
        cls.contact = Contact.objects.create(
            name="Contact", email="contact@example.com", company=cls.company
        )

    def test_rating_model_form_and_database_constraints(self):
        for status, rating in (
            ("scheduled", 4),
            ("completed", 0),
            ("completed", 6),
            ("bad", None),
        ):
            call = Call(
                agent=self.agent, contact=self.contact, status=status, rating=rating
            )
            with self.assertRaises(ValidationError):
                call.save()
            form = CallForm(
                data={"status": status, "rating": rating, "summary": ""}, instance=call
            )
            self.assertFalse(form.is_valid())
            with self.assertRaises(IntegrityError), transaction.atomic():
                Call.objects.bulk_create(
                    [
                        Call(
                            agent=self.agent,
                            contact=self.contact,
                            status=status,
                            rating=rating,
                        )
                    ]
                )

    def test_appointment_model_form_database_and_admin_reschedule(self):
        call = Call.objects.create(agent=self.agent, contact=self.contact)
        start = timezone.now()
        appointment = Appointment(call=call, starts_at=start, ends_at=start)
        with self.assertRaises(ValidationError):
            appointment.save()
        self.assertFalse(
            AppointmentForm(
                data={
                    "starts_at": start.isoformat(),
                    "ends_at": start.isoformat(),
                    "notes": "",
                },
                instance=appointment,
            ).is_valid()
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.bulk_create([appointment])
        appointment.ends_at = start + timedelta(hours=1)
        appointment.save()
        appointment.ends_at += timedelta(hours=1)
        appointment.save()
        call.refresh_from_db()
        self.assertEqual(call.status, "rescheduled")
        call.status = "completed"
        call.rating = 4
        call.save()
        appointment.notes = "Historical correction"
        appointment.ends_at += timedelta(hours=1)
        appointment.save()
        appointment.refresh_from_db()
        call.refresh_from_db()
        self.assertEqual(appointment.notes, "Historical correction")
        self.assertEqual(call.status, "completed")
        self.assertEqual(call.rating, 4)

    def test_cross_company_models_and_independent_company_reassignment(self):
        other = Contact.objects.create(
            name="Other", email="other@example.com", company=self.other_company
        )
        for model in (
            Call(agent=self.agent, contact=other),
            Email(agent=self.agent, contact=other, subject="Cross-company"),
        ):
            model.save()
            self.assertIsNotNone(model.pk)
        Call.objects.create(agent=self.agent, contact=self.contact)
        self.contact.company = self.other_company
        self.contact.save()
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.company, self.other_company)
        self.agent.companies.set([self.other_company])
        self.agent.refresh_from_db()
        self.assertEqual(
            list(self.agent.companies.values_list("id", flat=True)),
            [self.other_company.id],
        )

    def test_full_domain_and_social_accounts_registered_in_admin(self):
        for model in (Company, Contact, Agent, Call, Appointment, Email, SocialAccount):
            self.assertIn(model, admin.site._registry)


@override_settings(SECURE_SSL_REDIRECT=False)
class OIDCPageStylingTests(TestCase):
    def setUp(self):
        configured = configure_oidc(
            json.dumps(
                [
                    {
                        "id": "nublar",
                        "name": "Nublar",
                        "server_url": "https://issuer.example",
                        "client_id": "portal",
                        "client_secret": "test-value",
                    }
                ]
            ),
            debug=False,
        )
        self.settings_override = override_settings(SOCIALACCOUNT_PROVIDERS=configured)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def assert_styled(self, response, *, status=200):
        self.assertEqual(response.status_code, status)
        self.assertTemplateUsed(response, "allauth/layouts/base.html")
        self.assertContains(response, '<div class="auth-page">', status_code=status)
        self.assertContains(response, '<section class="auth-card">', status_code=status)
        self.assertContains(response, ".auth-card button", status_code=status)

    def test_errors_and_cancellation_share_styled_layout(self):
        for url, template, status in (
            ("/accounts/3rdparty/login/error/", "authentication_error", 401),
            ("/accounts/3rdparty/login/cancelled/", "login_cancelled", 200),
            ("/accounts/oidc/nublar/login/callback/", "authentication_error", 401),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assert_styled(response, status=status)
                self.assertTemplateUsed(response, f"socialaccount/{template}.html")

    def test_strict_cookie_callback_redirect_shares_styled_layout(self):
        with override_settings(SESSION_COOKIE_SAMESITE="Strict"):
            response = self.client.get("/accounts/oidc/nublar/login/callback/")
        self.assert_styled(response)
        self.assertTemplateUsed(response, "socialaccount/login_redirect.html")
        self.assertContains(response, 'http-equiv="refresh"')
        self.assertContains(response, "/accounts/oidc/nublar/login/callback/?_redir=")
        self.assertContains(response, "Continue")

    def test_social_signup_shares_styled_layout(self):
        sociallogin = SocialLogin(
            user=get_user_model()(username="new-agent"),
            account=SocialAccount(provider="nublar", uid="new-subject"),
        )
        with patch(
            "allauth.socialaccount.internal.flows.signup.get_pending_signup",
            return_value=sociallogin,
        ):
            response = self.client.get("/accounts/3rdparty/signup/")
        self.assert_styled(response)
        self.assertTemplateUsed(response, "socialaccount/signup.html")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_connections_and_connect_confirmation_share_styled_layout(self):
        user = get_user_model().objects.create_user("connected-agent")
        SocialAccount.objects.create(user=user, provider="nublar", uid="linked-subject")
        self.client.force_login(user)
        response = self.client.get("/accounts/3rdparty/")
        self.assert_styled(response)
        self.assertTemplateUsed(response, "socialaccount/connections.html")
        self.assertContains(response, 'name="account"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        response = self.client.get("/accounts/oidc/nublar/login/?process=connect")
        self.assert_styled(response)
        self.assertContains(response, "Connect Nublar")


class OIDCSettingsTests(TestCase):
    def test_authentication_templates_are_packaged_and_trigger_image_builds(self):
        root = Path(__file__).resolve().parent.parent.parent.parent
        self.assertIn("COPY backend/ backend/", (root / "Dockerfile").read_text())
        workflow = (root / ".github" / "workflows" / "build-image.yml").read_text()
        self.assertIn('- "backend/**"', workflow)

    def test_oidc_login_confirmation_uses_styled_layout(self):
        providers = self.providers()
        providers[0].update(id="nublar", name="Nublar")
        configured = configure_oidc(json.dumps(providers), debug=False)
        with override_settings(
            SOCIALACCOUNT_PROVIDERS=configured, SECURE_SSL_REDIRECT=False
        ):
            response = self.client.get("/accounts/oidc/nublar/login/?next=/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "allauth/layouts/base.html")
        self.assertContains(response, '<div class="auth-page">')
        self.assertContains(response, '<section class="auth-card">')
        self.assertContains(response, ".auth-card button")
        self.assertContains(response, "Nublar")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'method="post"')

    def test_documented_docker_environment_names(self):
        env = os.environ.copy()
        env.update(
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
            DJANGO_ALLOWED_HOSTS="portal.example, localhost",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://portal.example, https://second.example",
            DJANGO_SETTINGS_MODULE="config.settings",
        )
        env["PYTHONPATH"] = os.pathsep.join(
            (
                str(Path(__file__).resolve().parent.parent.parent),
                env.get("PYTHONPATH", ""),
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from django.conf import settings; "
                "assert settings.DEBUG is False; "
                "assert settings.ALLOWED_HOSTS == ['portal.example', 'localhost']; "
                "assert settings.CSRF_TRUSTED_ORIGINS == ['https://portal.example', 'https://second.example']; "
                "assert settings.STATIC_URL == '/static/'",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_secure_cookie_redirect_and_proxy_environment_defaults(self):
        env = os.environ.copy()
        env.update(
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
            DJANGO_SETTINGS_MODULE="config.settings",
        )
        env["PYTHONPATH"] = os.pathsep.join(
            (
                str(Path(__file__).resolve().parent.parent.parent),
                env.get("PYTHONPATH", ""),
            )
        )
        for name in (
            "DJANGO_SECURE_COOKIES",
            "DJANGO_SECURE_SSL_REDIRECT",
            "DJANGO_TRUST_PROXY",
        ):
            env.pop(name, None)
        cases = [
            ({}, True, False, None),
            ({"DJANGO_DEBUG": "1"}, False, False, None),
            ({"DJANGO_SECURE_COOKIES": "0"}, False, False, None),
            (
                {
                    "DJANGO_SECURE_COOKIES": "1",
                    "DJANGO_SECURE_SSL_REDIRECT": "1",
                    "DJANGO_TRUST_PROXY": "1",
                },
                True,
                True,
                ("HTTP_X_FORWARDED_PROTO", "https"),
            ),
        ]
        for changes, cookies, redirect, proxy in cases:
            process_env = env | changes
            code = (
                "from django.conf import settings; "
                f"assert settings.SESSION_COOKIE_SECURE is {cookies!r}; "
                f"assert settings.CSRF_COOKIE_SECURE is {cookies!r}; "
                f"assert settings.SECURE_SSL_REDIRECT is {redirect!r}; "
                f"assert settings.SECURE_PROXY_SSL_HEADER == {proxy!r}"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                env=process_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def providers(self):
        return [
            {
                "id": "work",
                "name": "Work",
                "server_url": "https://issuer.example/",
                "client_id": "work-client",
                "client_secret": "test-value",
            },
            {
                "id": "second",
                "name": "Second",
                "server_url": "https://second.example",
                "client_id": "second-client",
                "client_secret": "test-value",
            },
        ]

    def test_mapping_and_session_provider_urls(self):
        configured = configure_oidc(json.dumps(self.providers()), debug=False)
        apps = configured["openid_connect"]["APPS"]
        self.assertEqual(apps[0]["provider_id"], "work")
        self.assertEqual(apps[0]["settings"]["server_url"], "https://issuer.example")
        self.assertEqual(apps[1]["client_id"], "second-client")
        with override_settings(
            SOCIALACCOUNT_PROVIDERS=configured, SECURE_SSL_REDIRECT=False
        ):
            response = self.client.get("/api/session/").json()
            self.assertEqual(len(response["providers"]), 2)
            for provider in response["providers"]:
                self.assertEqual(
                    provider["login_url"],
                    reverse(
                        "openid_connect_login", kwargs={"provider_id": provider["id"]}
                    ),
                )
                self.assertEqual(
                    resolve(provider["login_url"]).url_name, "openid_connect_login"
                )
                adapter_provider = get_adapter().get_provider(
                    RequestFactory().get("/"), provider["id"]
                )
                self.assertEqual(adapter_provider.app.provider_id, provider["id"])

    def test_invalid_oidc_config_fails_closed(self):
        for raw in ("not json", "{}", "[{}]"):
            with self.assertRaises(ImproperlyConfigured):
                configure_oidc(raw, debug=False)
        for change in (
            {"server_url": "http://issuer.example"},
            {"server_url": "https://issuer.example?bad=1"},
            {"id": "../bad"},
            {"id": "bad id"},
            {"client_secret": ""},
        ):
            providers = self.providers()
            providers[0].update(change)
            with self.assertRaises(ImproperlyConfigured):
                configure_oidc(json.dumps(providers), debug=False)
        providers = self.providers()
        providers[1]["id"] = "work"
        with self.assertRaises(ImproperlyConfigured):
            configure_oidc(json.dumps(providers), debug=False)
        providers = self.providers()
        providers[0]["server_url"] = "http://localhost:8080"
        configure_oidc(json.dumps(providers), debug=True)

    def test_password_signup_disabled_social_signup_allowed_and_no_email_linking(self):
        self.assertFalse(AccountAdapter().is_open_for_signup(None))
        self.assertTrue(SocialAccountAdapter().is_open_for_signup(None, SocialLogin()))
        from django.conf import settings

        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION)
        self.assertFalse(settings.SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT)
        self.assertFalse(settings.SOCIALACCOUNT_LOGIN_ON_GET)

    def test_linked_social_subject_selects_existing_user_without_agent_assignment(self):
        user = get_user_model().objects.create_user(
            "linked", email="linked@example.com"
        )
        SocialAccount.objects.create(user=user, provider="work", uid="subject-123")
        sociallogin = SocialLogin(
            user=get_user_model()(email="linked@example.com"),
            account=SocialAccount(provider="work", uid="subject-123"),
        )
        sociallogin.lookup()
        self.assertEqual(sociallogin.user.pk, user.pk)
        self.assertFalse(Agent.objects.filter(user=user).exists())
        unlinked = SocialLogin(
            user=get_user_model()(email="linked@example.com"),
            account=SocialAccount(provider="work", uid="different-subject"),
            email_addresses=[EmailAddress(email="linked@example.com", verified=True)],
        )
        unlinked.lookup()
        self.assertFalse(unlinked.is_existing)
