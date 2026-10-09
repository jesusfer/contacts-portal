from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Appointment, Call, Company, Contact, Email


class ISODateTimeField(forms.DateTimeField):
    def to_python(self, value):
        if not isinstance(value, str):
            raise forms.ValidationError("Use an ISO 8601 timestamp with a timezone.")
        try:
            result = parse_datetime(value)
        except ValueError:
            result = None
        if result is None or timezone.is_naive(result):
            raise forms.ValidationError("Use an ISO 8601 timestamp with a timezone.")
        return result


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name"]


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["name", "email"]


class CallForm(forms.ModelForm):
    class Meta:
        model = Call
        fields = ["status", "summary", "rating"]


class AppointmentForm(forms.ModelForm):
    starts_at = ISODateTimeField()
    ends_at = ISODateTimeField()

    class Meta:
        model = Appointment
        fields = ["starts_at", "ends_at", "notes"]


class EmailForm(forms.ModelForm):
    received_at = ISODateTimeField()

    class Meta:
        model = Email
        fields = ["subject", "body", "received_at", "is_read", "is_replied"]
