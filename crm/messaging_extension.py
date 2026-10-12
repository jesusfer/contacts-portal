"""Customer search cards served through the authenticated Teams bot channel."""
import json
import re
from uuid import UUID

from asgiref.sync import async_to_sync, sync_to_async
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, CardFactory
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema import Activity, CardAction, HeroCard, InvokeResponse
from botbuilder.schema.teams import (
    MessagingExtensionAttachment,
    MessagingExtensionResponse,
    MessagingExtensionResult,
)
from botframework.connector.auth import AuthenticationConfiguration, AuthenticationConstants
from django.conf import settings
from django.db.models import Count, Prefetch
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from msrest.exceptions import DeserializationError

from .models import Agent, Company, Contact
from .teams import (
    TeamsConfigError, TeamsTokenError, get_teams_account, graph_provider, provider_tenant,
)


COMMAND_ID = "searchCustomers"
PAGE_SIZE = 25
CONTACT_LIMIT = 20
CARD_BYTE_LIMIT = 16000


def plain_text(value):
    return re.sub(r"([\\`*_{}\[\]()#+.!<>|~-])", r"\\\1", value)


def customer_card(company):
    body = [
        {"type": "TextBlock", "text": plain_text(company.name), "weight": "Bolder",
         "size": "Large", "wrap": True},
        {"type": "FactSet", "facts": [
            {"title": "Customer ID", "value": str(company.pk)},
            {"title": "Contacts", "value": str(company.contact_count)},
        ]},
    ]
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.3", "body": body,
    }
    shown = 0
    for contact in company.card_contacts:
        block = {"type": "TextBlock", "text": f"{plain_text(contact.name)}\n\n{plain_text(contact.email)}",
                 "wrap": True, "separator": True}
        body.append(block)
        if len(json.dumps(card).encode("utf-8")) > CARD_BYTE_LIMIT - 1024:
            body.pop()
            break
        shown += 1
    if not company.contact_count:
        body.append({"type": "TextBlock", "text": "No contacts recorded.", "wrap": True})
    elif shown < company.contact_count:
        body.append({"type": "TextBlock", "text": (
            f"Showing {shown} of {company.contact_count} contacts. Open the portal for the full directory."
        ), "wrap": True})
    return card


def authorize(activity):
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
        if not Agent.objects.filter(user=account.user).exists():
            raise TeamsTokenError("A provisioned CRM agent is required.")
    except (AttributeError, KeyError, TypeError, ValueError, TeamsConfigError) as exc:
        raise TeamsTokenError("A linked, active CRM agent in the configured tenant is required.") from exc


def search_customers(text, skip, count):
    return list(
        Company.objects.filter(name__icontains=text).annotate(
            contact_count=Count("contacts"),
        )[skip:skip + count]
    )


def selected_customer(pk):
    return Company.objects.annotate(contact_count=Count("contacts")).prefetch_related(Prefetch(
        "contacts", queryset=Contact.objects.all()[:CONTACT_LIMIT], to_attr="card_contacts",
    )).filter(pk=pk).first()


def result_response(attachments):
    response = MessagingExtensionResponse(compose_extension=MessagingExtensionResult(
        type="result", attachment_layout="list", attachments=attachments,
    ))
    return InvokeResponse(status=200, body=response.serialize())


class CustomerMessagingExtension(TeamsActivityHandler):
    async def on_invoke_activity(self, turn_context):
        if turn_context.activity.name not in ("composeExtension/query", "composeExtension/selectItem"):
            return InvokeResponse(status=501)
        try:
            await sync_to_async(authorize)(turn_context.activity)
        except TeamsTokenError:
            return InvokeResponse(status=403)
        value = turn_context.activity.value
        if turn_context.activity.name == "composeExtension/selectItem":
            pk = value.get("customer_id") if isinstance(value, dict) else None
            if type(pk) is not int or not 0 < pk < 2**63:
                return InvokeResponse(status=400)
            company = await sync_to_async(selected_customer)(pk)
            if not company:
                return InvokeResponse(status=404)
            card = CardFactory.adaptive_card(customer_card(company))
            return result_response([MessagingExtensionAttachment(
                content_type=card.content_type, content=card.content,
            )])
        if (
            not isinstance(value, dict) or value.get("commandId") != COMMAND_ID
            or not isinstance(value.get("parameters"), list)
        ):
            return InvokeResponse(status=400)
        parameters = value["parameters"]
        if (
            len(parameters) != 1 or not isinstance(parameters[0], dict)
            or parameters[0].get("name") != "searchText"
            or not isinstance(parameters[0].get("value"), str)
            or len(parameters[0]["value"]) > 200
        ):
            return InvokeResponse(status=400)
        options = value.get("queryOptions") or {}
        if not isinstance(options, dict):
            return InvokeResponse(status=400)
        skip, count = options.get("skip", 0), options.get("count", PAGE_SIZE)
        if (
            type(skip) is not int or not 0 <= skip <= 10000
            or type(count) is not int or not 1 <= count <= PAGE_SIZE
        ):
            return InvokeResponse(status=400)
        companies = await sync_to_async(search_customers)(
            parameters[0]["value"].strip(), skip, count,
        )
        attachments = []
        for company in companies:
            preview = CardFactory.hero_card(HeroCard(
                title=plain_text(company.name[:80] + ("…" if len(company.name) > 80 else "")),
                subtitle=f"{company.contact_count} contacts",
                tap=CardAction(type="invoke", value={"customer_id": company.pk}),
            ))
            attachments.append(MessagingExtensionAttachment(
                content_type=preview.content_type, content=preview.content, preview=preview,
            ))
        return result_response(attachments)


async def channel_claims_only(claims):
    # Emulator/skill tokens must never be allowed to impersonate a Teams user.
    if claims.get("iss") != AuthenticationConstants.TO_BOT_FROM_CHANNEL_TOKEN_ISSUER:
        raise PermissionError("A Bot Framework channel token is required.")


class TeamsChannelAdapter(BotFrameworkAdapter):
    async def _authenticate_request(self, activity, authorization):
        try:
            return await super()._authenticate_request(activity, authorization)
        except Exception as exc:
            # The SDK also raises generic exceptions for invalid signing keys.
            raise PermissionError("Bot Framework authentication failed.") from exc


async def process_activity(activity, authorization):
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
            activity.type != "invoke" or activity.channel_id != "msteams"
            or not isinstance(activity.service_url, str)
            or not activity.service_url.startswith("https://")
            or not isinstance(activity.channel_data, dict)
            or ("tenant" in activity.channel_data and not isinstance(activity.channel_data["tenant"], dict))
            or not isinstance(activity.value, dict)
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
        else HttpResponse(status=response.status if response else 500)
    )
    result["Cache-Control"] = "private, no-store"
    return result
