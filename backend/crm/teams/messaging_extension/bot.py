"""Teams bot channel adapter and activity dispatcher."""
import json
from uuid import UUID

from asgiref.sync import async_to_sync, sync_to_async
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema import Activity, InvokeResponse
from botframework.connector.auth import AuthenticationConfiguration, AuthenticationConstants
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from msrest.exceptions import DeserializationError

from ...models import Agent
from ..auth import (
    TeamsConfigError, TeamsTokenError, get_teams_account, graph_provider, provider_tenant,
)
from .action import handle_action
from .search import handle_search


def authorize(activity):
    """Require the activity sender to match a provisioned agent in the configured tenant.

    Returns the matching Agent."""
    try:
        tenant = str(UUID(activity.channel_data["tenant"]["id"]))
        oid = str(UUID(activity.from_property.aad_object_id))
        configured_tenant = str(UUID(settings.TEAMS_BOT_TENANT_ID))
        app = graph_provider(settings.TEAMS_BOT_PROVIDER_ID)
        if (
            tenant != configured_tenant or not app
            or provider_tenant(app, tenant) != tenant
        ):
            raise TeamsTokenError("Unauthorized Teams tenant.")
        account = get_teams_account(app["provider_id"], {"oid": oid, "tid": tenant})
        agent = Agent.objects.filter(user=account.user).first()
        if not agent:
            raise TeamsTokenError("A provisioned CRM agent is required.")
        return agent
    except (AttributeError, KeyError, TypeError, ValueError, TeamsConfigError) as exc:
        raise TeamsTokenError("A linked, active CRM agent in the configured tenant is required.") from exc


class CustomerMessagingExtension(TeamsActivityHandler):
    async def on_message_activity(self, turn_context):
        await turn_context.send_activity("This bot is not enabled for conversation.")

    async def on_invoke_activity(self, turn_context):
        """Authorize and handle supported customer-search and card-selection invokes."""
        if turn_context.activity.name not in (
            "composeExtension/query", "composeExtension/selectItem",
            "composeExtension/fetchTask", "composeExtension/submitAction",
        ):
            return InvokeResponse(status=501)
        try:
            agent = await sync_to_async(authorize)(turn_context.activity)
        except TeamsTokenError:
            return InvokeResponse(status=403)
        value = turn_context.activity.value
        if turn_context.activity.name in ("composeExtension/fetchTask", "composeExtension/submitAction"):
            return await sync_to_async(handle_action)(agent, turn_context.activity.name, value)
        return await handle_search(turn_context.activity)


async def channel_claims_only(claims):
    """Reject Bot Framework tokens that did not originate from a Teams channel."""
    # Emulator/skill tokens must never be allowed to impersonate a Teams user.
    if claims.get("iss") != AuthenticationConstants.TO_BOT_FROM_CHANNEL_TOKEN_ISSUER:
        raise PermissionError("A Bot Framework channel token is required.")


class TeamsChannelAdapter(BotFrameworkAdapter):
    async def _authenticate_request(self, activity, authorization):
        """Translate Bot Framework authentication failures into endpoint-safe errors."""
        try:
            return await super()._authenticate_request(activity, authorization)
        except Exception as exc:
            # The SDK also raises generic exceptions for invalid signing keys.
            raise PermissionError("Bot Framework authentication failed.") from exc


async def process_activity(activity, authorization):
    """Process an authenticated Teams activity with the customer messaging extension."""
    adapter = TeamsChannelAdapter(BotFrameworkAdapterSettings(
        app_id=settings.TEAMS_BOT_APP_ID,
        app_password=settings.TEAMS_BOT_APP_PASSWORD,
        channel_auth_tenant=settings.TEAMS_BOT_TENANT_ID,
        auth_configuration=AuthenticationConfiguration(claims_validator=channel_claims_only),
    ))
    return await adapter.process_activity(activity, authorization, CustomerMessagingExtension().on_turn)


@csrf_exempt
@require_POST
def messages(request):
    """Validate and dispatch a Bot Framework message or invoke to the Teams extension."""
    if not all((
        settings.TEAMS_BOT_APP_ID, settings.TEAMS_BOT_APP_PASSWORD,
        settings.TEAMS_BOT_TENANT_ID, settings.TEAMS_BOT_PROVIDER_ID,
    )):
        return JsonResponse({"error": "Teams messaging extension is not configured."}, status=503)
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        return HttpResponse(status=401)
    if request.content_type != "application/json":
        return HttpResponse(status=415)
    if len(request.body) > 65536:
        return HttpResponse(status=413)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        activity = Activity().deserialize(data)
        if (
            activity.type not in ("invoke", "message") or activity.channel_id != "msteams"
            or not isinstance(activity.service_url, str)
            or not activity.service_url.startswith("https://")
            or not isinstance(activity.channel_data, dict)
            or ("tenant" in activity.channel_data and not isinstance(activity.channel_data["tenant"], dict))
            or (activity.type == "invoke" and not isinstance(activity.value, dict))
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError, KeyError, DeserializationError):
        return HttpResponse(status=400)
    try:
        response = async_to_sync(process_activity)(activity, authorization)
    except PermissionError:
        return HttpResponse(status=401)
    result = (
        JsonResponse(response.body, status=response.status)
        if response and response.body is not None
        else HttpResponse(status=response.status if response else (
            200 if activity.type == "message" else 500
        ))
    )
    result["Cache-Control"] = "private, no-store"
    return result
