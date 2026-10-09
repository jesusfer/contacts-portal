from django.contrib import admin

from .models import Agent, Appointment, Call, Company, Contact, Email


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    search_fields = ["name"]
    list_display = ["name", "id"]


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "company"]
    list_filter = ["company"]
    search_fields = ["name", "email", "company__name"]
    autocomplete_fields = ["company"]


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ["user", "company"]
    list_filter = ["company"]
    search_fields = ["user__username", "company__name"]
    autocomplete_fields = ["user", "company"]


class AppointmentInline(admin.StackedInline):
    model = Appointment
    extra = 0


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ["contact", "agent", "status", "rating"]
    list_filter = ["status", "agent__company"]
    search_fields = ["contact__name", "agent__user__username", "summary"]
    autocomplete_fields = ["agent", "contact"]
    inlines = [AppointmentInline]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["call", "starts_at", "ends_at"]
    search_fields = ["call__contact__name", "notes"]
    autocomplete_fields = ["call"]


@admin.register(Email)
class EmailAdmin(admin.ModelAdmin):
    list_display = ["subject", "contact", "agent", "received_at", "is_read", "is_replied"]
    list_filter = ["is_read", "is_replied", "agent__company"]
    search_fields = ["subject", "body", "contact__email", "agent__user__username"]
    autocomplete_fields = ["agent", "contact"]
