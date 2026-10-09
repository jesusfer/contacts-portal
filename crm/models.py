from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def profile_picture_path(instance, filename):
    return f"profile-pictures/{uuid4().hex}.jpg"


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    picture = models.FileField(upload_to=profile_picture_path, blank=True)


class ValidatedModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Company(ValidatedModel):
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["name", "id"]
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class Contact(ValidatedModel):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="contacts")

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

class Agent(ValidatedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="agents")

    def __str__(self):
        return self.user.get_username()

class Call(ValidatedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        MISSED = "missed", "Missed"
        RESCHEDULED = "rescheduled", "Rescheduled"
        CANCELLED = "cancelled", "Cancelled"

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="calls")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="calls")
    status = models.CharField(max_length=20, choices=Status, default=Status.SCHEDULED)
    was_rescheduled = models.BooleanField(default=False, editable=False)
    summary = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__isnull=True) | Q(status="completed", rating__gte=1, rating__lte=5),
                name="call_rating_completed_1_to_5",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["scheduled", "completed", "missed", "rescheduled", "cancelled"]),
                name="call_valid_status",
            ),
        ]

    def clean(self):
        super().clean()
        if self.rating is not None and self.status != self.Status.COMPLETED:
            raise ValidationError({"rating": "Only completed calls may have a rating."})

    def save(self, *args, **kwargs):
        if self.status == self.Status.RESCHEDULED or (
            self.pk and type(self).objects.filter(pk=self.pk, was_rescheduled=True).exists()
        ):
            self.was_rescheduled = True
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"was_rescheduled"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.contact} — {self.get_status_display()}"


class Appointment(ValidatedModel):
    call = models.OneToOneField(Call, on_delete=models.CASCADE, related_name="appointment")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["starts_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="appointment_end_after_start")
        ]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End time must be after start time."})

    def save(self, *args, **kwargs):
        from django.db import transaction

        with transaction.atomic():
            self.full_clean()
            previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            changed = previous and (
                previous.starts_at != self.starts_at or previous.ends_at != self.ends_at
            )
            result = super().save(*args, **kwargs)
            if changed:
                call = Call.objects.select_for_update().get(pk=self.call_id)
                if call.status in (Call.Status.SCHEDULED, Call.Status.RESCHEDULED):
                    call.status = Call.Status.RESCHEDULED
                    call.rating = None
                    call.save(update_fields=["status", "rating"])
            return result

    def __str__(self):
        return f"{self.call} at {self.starts_at}"


class Email(ValidatedModel):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="emails")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="emails")
    subject = models.CharField(max_length=300)
    body = models.TextField(blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)
    is_replied = models.BooleanField(default=False)

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return self.subject
