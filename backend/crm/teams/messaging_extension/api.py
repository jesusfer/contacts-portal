"""Contact cards for the API-based Teams messaging extension."""
from datetime import UTC

from django.conf import settings
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ...models import Agent, Call, Contact
from ..auth import (
    TeamsConfigError, TeamsTokenError, get_teams_account, graph_provider,
    load_teams_config, validate_teams_token,
)
from .text import plain_text


RESULT_LIMIT = 25
SUMMARY_LIMIT = 200


def response(data, status=200):
    """Create a non-cacheable JSON response, adding a bearer challenge when needed."""
    result = JsonResponse(data, status=status)
    result["Cache-Control"] = "private, no-store"
    if status == 401:
        result["WWW-Authenticate"] = "Bearer"
    return result


def call_history(calls):
    """Format an agent's recent call records for display in a Teams contact card."""
    entries = []
    for call in calls:
        details = [f"Call #{call.pk}", call.get_status_display()]
        if hasattr(call, "appointment"):
            details.append(call.appointment.starts_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"))
        if call.rating is not None:
            details.append(f"Rating: {call.rating}/5")
        summary = call.summary[:SUMMARY_LIMIT]
        if len(call.summary) > SUMMARY_LIMIT:
            summary += "… (summary shortened)"
        entries.append(plain_text(" · ".join(details) + ("\n\n" + summary if summary else "")))
    return "\n\n".join(entries) or "No calls recorded."


@require_GET
def search_contacts(request):
    """Authenticate a delegated Teams token and return matching contacts with recent calls."""
    app = graph_provider(settings.TEAMS_API_PROVIDER_ID)
    try:
        if not app:
            raise TeamsConfigError("A Teams API provider is required.")
        config = load_teams_config(app["settings"])
    except TeamsConfigError:
        return response({"error": "Teams API messaging extension is not configured."}, 503)

    authorization = request.headers.get("Authorization", "").split()
    if (
        len(authorization) != 2 or authorization[0].lower() != "bearer"
        or len(authorization[1]) > 16384
    ):
        return response({"error": "A delegated bearer token is required."}, 401)
    try:
        claims = validate_teams_token(authorization[1], config)
    except TeamsTokenError:
        return response({"error": "Invalid access token."}, 401)
    try:
        account = get_teams_account(app["provider_id"], claims)
        agent = Agent.objects.get(user=account.user)
    except (TeamsTokenError, Agent.DoesNotExist):
        return response({"error": "A linked, active CRM agent is required."}, 403)

    queries = request.GET.getlist("query")
    if len(queries) != 1 or not queries[0].strip() or len(queries[0]) > 200:
        return response({"error": "query must contain between 1 and 200 characters."}, 400)
    contacts = Contact.objects.select_related("company")
    for term in queries[0].split():
        contacts = contacts.filter(
            Q(name__icontains=term) | Q(email__icontains=term) | Q(company__name__icontains=term)
        )
    contacts = contacts.prefetch_related(Prefetch(
        "calls",
        queryset=Call.objects.filter(agent=agent).select_related("appointment").order_by("-id")[:3],
        to_attr="recent_calls",
    ))[:RESULT_LIMIT]
    return response({"contacts": [
        {
            "name": plain_text(contact.name),
            "email": plain_text(contact.email),
            "customer": plain_text(contact.company.name),
            "calls": call_history(contact.recent_calls),
        }
        for contact in contacts
    ]})
