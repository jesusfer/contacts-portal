from datetime import timedelta
import shlex

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from crm.models import Agent, Appointment, Call, Company, Contact, Email


class Command(BaseCommand):
    help = "Opt in to fictional demo contacts, calls, appointments, and inbox emails."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="demo-agent")

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("Username must not be blank.")
        try:
            user_model = get_user_model()
            user_model._meta.get_field(user_model.USERNAME_FIELD).clean(username, None)
            with transaction.atomic():
                result = self.seed(user_model, username)
        except ValidationError as exc:
            raise CommandError(f"Cannot seed demo data: {exc}") from exc

        if result is None:
            self.stdout.write(self.style.WARNING(
                f"Skipped demo data for {username}: agent already has calls or emails; "
                "no changes made."
            ))
            return
        created_user, counts = result
        self.stdout.write(self.style.SUCCESS(
            f"Seeded demo data for {username}: created "
            + ", ".join(f"{count} {name}" for name, count in counts.items())
            + "."
        ))
        if created_user:
            self.stdout.write(
                "Login is disabled until you set a password: "
                f"python manage.py changepassword {shlex.quote(username)}"
            )

    def seed(self, user_model, username):
        user = user_model.objects.select_for_update().filter(
            **{user_model.USERNAME_FIELD: username}
        ).first()
        agent = Agent.objects.select_for_update().filter(user=user).first() if user else None
        if agent and (agent.calls.exists() or agent.emails.exists()):
            return None

        created_user = user is None
        if created_user:
            user = user_model(
                **{user_model.USERNAME_FIELD: username},
                is_active=True, is_staff=False, is_superuser=False,
            )
            user.set_unusable_password()
            user.full_clean()
            user.save()

        counts = dict.fromkeys(("companies", "contacts", "calls", "appointments", "emails"), 0)
        if agent is None:
            employer, created = Company.objects.get_or_create(name="Demo Harbor Advisory")
            counts["companies"] += int(created)
            agent = Agent.objects.create(user=user)
            agent.companies.add(employer)

        contacts = []
        for company_name, people in (
            ("Demo Cedar Manufacturing", (
                ("Maya Reed", "maya.reed@example.com"),
                ("Owen Brooks", "owen.brooks@example.com"),
            )),
            ("Demo Lumen Studios", (
                ("Nora Ellis", "nora.ellis@example.com"),
                ("Leo Mercer", "leo.mercer@example.com"),
            )),
        ):
            company, created = Company.objects.get_or_create(name=company_name)
            counts["companies"] += int(created)
            for name, email in people:
                contact, created = Contact.objects.get_or_create(
                    name=name, email=email, company=company,
                )
                counts["contacts"] += int(created)
                contacts.append(contact)

        now = timezone.now()
        for contact_index, status, hours, summary, rating, previously_rescheduled in (
            (0, Call.Status.COMPLETED, -72,
             "Reviewed the pilot budget; Maya will send procurement requirements.", 5, False),
            (2, Call.Status.COMPLETED, -48,
             "Confirmed the revised rollout after moving the original meeting.", 3, True),
            (1, Call.Status.COMPLETED, -24, "", None, False),
            (3, Call.Status.SCHEDULED, -3, "", None, False),
            (0, Call.Status.SCHEDULED, 24,
             "Discuss the procurement checklist and next steps.", None, False),
            (2, Call.Status.SCHEDULED, 48,
             "Review the studio onboarding plan.", None, False),
            (1, Call.Status.RESCHEDULED, 72,
             "Moved the equipment review to accommodate the production team.", None, False),
            (3, Call.Status.MISSED, -96,
             "Leo could not join; follow up by email to find a new time.", None, False),
            (0, Call.Status.CANCELLED, -120,
             "The duplicate discovery session was cancelled at Maya's request.", None, False),
        ):
            call = Call.objects.create(
                agent=agent, contact=contacts[contact_index],
                status=Call.Status.RESCHEDULED if previously_rescheduled else status,
                summary=summary, rating=None if previously_rescheduled else rating,
            )
            if previously_rescheduled:
                call.status = status
                call.rating = rating
                call.save(update_fields=["status", "rating"])
            start = now + timedelta(hours=hours)
            Appointment.objects.create(
                call=call, starts_at=start, ends_at=start + timedelta(minutes=30),
                notes=f"Demo meeting with {call.contact.name}.",
            )
            counts["calls"] += 1
            counts["appointments"] += 1

        for contact_index, subject, body, hours, is_read, is_replied in (
            (0, "Pilot procurement checklist",
             "Hello, could you review our fictional pilot checklist before tomorrow's call?",
             -2, False, False),
            (2, "Studio onboarding questions",
             "Our demo studio team would like to discuss training dates and onboarding.",
             -8, True, False),
            (1, "Equipment review confirmation",
             "Thanks for confirming the revised time for our demo equipment review.",
             -26, True, True),
            (3, "Follow-up on missed meeting",
             "Thank you for your reply. I will review the proposed demo meeting times.",
             -50, False, True),
        ):
            Email.objects.create(
                agent=agent, contact=contacts[contact_index], subject=subject, body=body,
                received_at=now + timedelta(hours=hours),
                is_read=is_read, is_replied=is_replied,
            )
            counts["emails"] += 1
        return created_user, counts
