"""HTTP views for Teams configuration, sign-in, and customer access."""
from allauth.core import ratelimit
from django.conf import settings
from django.contrib.auth import login
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie

from ..graph import GraphClient
from ..models import Company
from ..signals import AUTH_SOURCE_SESSION_KEY
from .auth import (
    GraphTokenManager, TeamsConfigError, TeamsTokenError, exchange_graph_token,
    get_teams_account, graph_provider, load_teams_config, provider_tenant, validate_teams_token,
)
from ..views import (
    TEXT, api, call_json, company_json, contact_json, directory_contacts,
    error, graph_profile_json, payload, scoped_calls, session_json,
)


@ensure_csrf_cookie
@api(["GET"], agent_required=False)
def teams_config(request):
    """Expose configured Teams providers for the Teams tab and SSO client."""
    providers = settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get("APPS", [])
    return JsonResponse({"graph_only": settings.TEAMS_GRAPH_ONLY, "providers": [{
        "id": app["provider_id"], "provider_id": app["provider_id"], "name": app["name"],
        "client_id": app["client_id"], "resource": app["settings"]["teams"]["resource"],
        "tenant_id": app["settings"]["teams"]["tenant_id"],
    } for app in providers if graph_provider(app["provider_id"]) and app["settings"].get("teams")]})


@api(["POST"], agent_required=False)
def teams_sso(request):
    """Exchange a Teams SSO token for CRM and delegated Graph authentication."""
    return _teams_auth(request)


@api(["POST"], agent_required=False)
def teams_identity(request):
    """Validate a Teams SSO token and return its identity without CRM sign-in."""
    return _teams_auth(request, graph_only=True)


def teams_crm_authorized(request):
    """Check that the session and requested provider have an active Teams CRM sign-in."""
    provider_id = request.session.get(AUTH_SOURCE_SESSION_KEY)
    app = graph_provider(provider_id)
    return (
        request.session.get("crm_teams_authenticated") is True
        and app is not None and bool(app["settings"].get("teams"))
        and request.GET.get("provider") == provider_id
    )


@api(["GET"])
def teams_customers(request):
    """Return the customer directory to an authorized Teams CRM tab."""
    if not teams_crm_authorized(request):
        return error("Sign in to the CRM with Teams SSO first.", 403)
    return JsonResponse({"customers": [company_json(item) for item in Company.objects.all()]})


@api(["GET"])
def teams_customer(request, pk):
    """Return one customer's details and agent-scoped calls for an authorized Teams tab."""
    if not teams_crm_authorized(request):
        return error("Sign in to the CRM with Teams SSO first.", 403)
    company = get_object_or_404(Company, pk=pk)
    return JsonResponse({
        "customer": company_json(company),
        "contacts": [contact_json(item) for item in directory_contacts().filter(company=company)],
        "calls": [call_json(item) for item in scoped_calls(request).filter(contact__company=company)],
    })


def _teams_auth(request, *, graph_only=False):
    """Validate Teams SSO, optionally sign in to the CRM, and establish Graph access."""
    if len(request.body) > 32768:
        return error("Teams SSO request is too large.", 400)
    data = payload(request, {"provider_id": TEXT, "token": TEXT}, required=("provider_id", "token"))
    if not 256 <= len(data["token"]) <= 16384 or not 1 <= len(data["provider_id"]) <= 255:
        return error("Invalid Teams SSO input size.", 400)
    provider_id = data["provider_id"]
    if not ratelimit.consume(request, action="login"):
        response = error("Teams SSO rate limited. Try again later.", 429)
        response["Retry-After"] = "300"
        return response
    app = graph_provider(provider_id)
    if not app or not app["settings"].get("teams"):
        return error("Teams provider not found.", 404)
    try:
        config = load_teams_config(app["settings"])
        claims = validate_teams_token(data["token"], config)
        tenant = provider_tenant(app, claims["tid"])
        if graph_only:
            return JsonResponse({"id": claims["oid"], "tenant_id": tenant})
        account = get_teams_account(provider_id, claims)
        token = exchange_graph_token(app, assertion=data["token"], tenant_id=tenant)
        profile_data = GraphClient(token["access_token"], tenant).get_profile()
        if profile_data.get("id") != claims["oid"]:
            raise TeamsTokenError("Graph identity does not match the Teams identity.")
        login(request, account.user, backend="allauth.account.auth_backends.AuthenticationBackend")
        request.session[AUTH_SOURCE_SESSION_KEY] = provider_id
        GraphTokenManager.store_token(
            request, provider_id, token["access_token"], token.get("refresh_token"),
            token["expires_in"], tenant, oid=claims["oid"],
        )
        request.session["crm_teams_authenticated"] = True
        graph_profile_json(request, profile_data)
        return JsonResponse({
            **session_json(request),
            "teams_identity": {"id": claims["oid"], "tenant_id": tenant},
        })
    except TeamsTokenError as exc:
        return error(exc.public_message, 403)
    except TeamsConfigError:
        return error("Teams provider configuration unavailable.", 502)
