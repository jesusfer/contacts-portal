from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ..models import Agent, Appointment, Call, Company, Contact, Email


class SeedDemoTests(TestCase):
    def seed(self, **options):
        output = StringIO()
        call_command("seed_demo", stdout=output, **options)
        return output.getvalue()

    def snapshot(self):
        return {
            model.__name__: list(model.objects.order_by("pk").values())
            for model in (
                get_user_model(),
                Agent,
                Company,
                Contact,
                Call,
                Appointment,
                Email,
            )
        }

    def existing_agent(self, **flags):
        user = get_user_model().objects.create_user(username="existing", **flags)
        user.set_password("existing-test-passphrase")
        user.save()
        company = Company.objects.create(name="Operational employer")
        agent = Agent.objects.create(user=user)
        agent.companies.add(company)
        return agent

    def test_creates_valid_cross_company_dataset(self):
        now = timezone.now()
        with patch("crm.management.commands.seed_demo.timezone.now", return_value=now):
            output = self.seed()
        agent = Agent.objects.get(user__username="demo-agent")
        self.assertEqual(
            [
                Company.objects.count(),
                Contact.objects.count(),
                Call.objects.count(),
                Appointment.objects.count(),
                Email.objects.count(),
            ],
            [3, 4, 9, 9, 4],
        )
        self.assertTrue(
            all(
                name.startswith("Demo ")
                for name in Company.objects.values_list("name", flat=True)
            )
        )
        self.assertEqual(
            set(Contact.objects.values_list("email", flat=True)),
            {
                "maya.reed@example.com",
                "owen.brooks@example.com",
                "nora.ellis@example.com",
                "leo.mercer@example.com",
            },
        )
        self.assertEqual(Contact.objects.values("company").distinct().count(), 2)
        self.assertFalse(
            Contact.objects.filter(company__in=agent.companies.all()).exists()
        )
        self.assertEqual(
            set(agent.calls.values_list("status", flat=True)), set(Call.Status.values)
        )
        completed = agent.calls.filter(status=Call.Status.COMPLETED)
        self.assertEqual(completed.count(), 3)
        self.assertEqual(set(completed.values_list("rating", flat=True)), {3, 5, None})
        self.assertTrue(completed.filter(summary="").exists())
        self.assertTrue(completed.filter(was_rescheduled=True).exists())
        self.assertFalse(
            agent.calls.exclude(status=Call.Status.COMPLETED)
            .exclude(rating=None)
            .exists()
        )
        self.assertTrue(
            agent.calls.filter(
                status=Call.Status.RESCHEDULED, was_rescheduled=True
            ).exists()
        )
        self.assertTrue(
            agent.calls.filter(
                status=Call.Status.SCHEDULED, appointment__ends_at__lt=now
            ).exists()
        )
        for status in (Call.Status.SCHEDULED, Call.Status.RESCHEDULED):
            self.assertTrue(
                agent.calls.filter(
                    status=status, appointment__starts_at__gt=now
                ).exists()
            )
        self.assertEqual(
            set(agent.emails.values_list("is_read", "is_replied")),
            {
                (False, False),
                (True, False),
                (True, True),
                (False, True),
            },
        )
        self.assertEqual(agent.emails.values("contact__company").distinct().count(), 2)
        for model in (Company, Contact, Agent, Call, Appointment, Email):
            for instance in model.objects.all():
                instance.full_clean()
        for appointment in Appointment.objects.all():
            self.assertGreater(appointment.ends_at, appointment.starts_at)
        for email in agent.emails.all():
            self.assertTrue(email.body)
            self.assertLess(email.received_at, now)
        self.assertIn(
            "3 companies, 4 contacts, 9 calls, 9 appointments, 4 emails", output
        )
        self.assertIn("demo-agent", output)

    def test_new_user_has_no_password_or_elevated_flags(self):
        output = self.seed(username="  custom-demo  ")
        user = get_user_model().objects.get(username="custom-demo")
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIn("python manage.py changepassword custom-demo", output)
        self.assertNotIn(user.password, output)

    def test_repeat_preserves_all_rows_and_dates(self):
        self.seed()
        before = self.snapshot()
        with patch(
            "crm.management.commands.seed_demo.timezone.now",
            return_value=timezone.now() + timedelta(days=30),
        ):
            output = self.seed()
        self.assertEqual(self.snapshot(), before)
        self.assertIn("Skipped", output)
        self.assertIn("no changes made", output)
        self.assertNotIn("changepassword", output)

    def test_existing_empty_agent_preserves_company_password_and_flags(self):
        agent = self.existing_agent(is_active=False, is_staff=True, is_superuser=True)
        before_user = get_user_model().objects.values().get(pk=agent.user_id)
        before_companies = list(agent.companies.values().order_by("pk"))
        output = self.seed(username="existing")
        agent.refresh_from_db()
        self.assertEqual(
            get_user_model().objects.values().get(pk=agent.user_id), before_user
        )
        self.assertEqual(
            list(agent.companies.values().order_by("pk")), before_companies
        )
        self.assertEqual(agent.calls.count(), 9)
        self.assertFalse(Company.objects.filter(name="Demo Harbor Advisory").exists())
        self.assertNotIn("changepassword", output)

    def test_existing_user_without_agent_preserves_credentials_and_flags(self):
        user = get_user_model().objects.create_user(
            username="existing",
            is_active=False,
            is_staff=True,
            is_superuser=True,
        )
        user.set_password("existing-test-passphrase")
        user.save()
        before = get_user_model().objects.values().get(pk=user.pk)
        output = self.seed(username="existing")
        self.assertEqual(get_user_model().objects.values().get(pk=user.pk), before)
        self.assertIn(
            "Demo Harbor Advisory",
            [company.name for company in Agent.objects.get(user=user).companies.all()],
        )
        self.assertNotIn("changepassword", output)

    def test_existing_calls_or_emails_skip_without_any_mutations(self):
        agent = self.existing_agent(is_staff=True)
        contact = Contact.objects.create(
            company=agent.companies.first(),
            name="Operational contact",
            email="operational@example.com",
        )
        for model in (Call, Email):
            with self.subTest(activity=model.__name__):
                activity = model.objects.create(
                    agent=agent,
                    contact=contact,
                    **({"subject": "Operational email"} if model is Email else {}),
                )
                before = self.snapshot()
                output = self.seed(username="existing")
                self.assertEqual(self.snapshot(), before)
                self.assertIn("Skipped", output)
                self.assertNotIn("changepassword", output)
                activity.delete()

    def test_shared_directory_reused_and_unrelated_activity_preserved(self):
        other_agent = self.existing_agent()
        contact = Contact.objects.create(
            company=other_agent.companies.first(),
            name="Unrelated contact",
            email="unrelated@example.com",
        )
        call = Call.objects.create(agent=other_agent, contact=contact)
        email = Email.objects.create(
            agent=other_agent, contact=contact, subject="Unrelated email"
        )
        before = self.snapshot()
        self.seed()
        after_first = self.snapshot()
        self.seed(username="second-demo")
        self.assertEqual(Company.objects.count(), 4)
        self.assertEqual(Contact.objects.count(), 5)
        self.assertEqual(
            Call.objects.filter(agent__user__username="second-demo").count(), 9
        )
        for model in (Company, Contact):
            self.assertEqual(
                self.snapshot()[model.__name__], after_first[model.__name__]
            )
        for name, rows in before.items():
            for row in rows:
                self.assertIn(row, self.snapshot()[name])
        self.assertEqual(Call.objects.get(pk=call.pk).agent_id, other_agent.pk)
        self.assertEqual(Email.objects.get(pk=email.pk).subject, "Unrelated email")

    def test_invalid_username_makes_no_writes(self):
        for username in ("", " \t ", "invalid username!", "x" * 151):
            with self.subTest(username=username):
                before = self.snapshot()
                with self.assertRaises(CommandError):
                    self.seed(username=username)
                self.assertEqual(self.snapshot(), before)

    def test_late_validation_failure_rolls_back_entire_seed(self):
        agent = self.existing_agent()
        contact = Contact.objects.create(
            company=agent.companies.first(),
            name="Existing contact",
            email="existing@example.com",
        )
        call = Call.objects.create(agent=agent, contact=contact)
        start = timezone.now() + timedelta(days=1)
        Appointment.objects.create(
            call=call,
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
        )
        Email.objects.create(agent=agent, contact=contact, subject="Existing email")
        before = self.snapshot()

        def reject_email(**kwargs):
            demo_agent = Agent.objects.get(user__username="demo-agent")
            self.assertEqual(demo_agent.calls.count(), 9)
            self.assertEqual(
                Appointment.objects.filter(call__agent=demo_agent).count(), 9
            )
            raise ValidationError("Rejected email")

        with patch(
            "crm.management.commands.seed_demo.Email.objects.create",
            side_effect=reject_email,
        ):
            with self.assertRaises(CommandError):
                self.seed()
        self.assertEqual(self.snapshot(), before)
