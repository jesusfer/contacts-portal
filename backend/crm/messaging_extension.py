"""Customer search cards served through the authenticated Teams bot channel."""
import html
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
from django.utils.html import strip_tags
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from msrest.exceptions import DeserializationError

from .models import Agent, Call, Company, Contact
from .teams import (
    TeamsConfigError, TeamsTokenError, get_teams_account, graph_provider, provider_tenant,
)


COMMAND_ID = "searchCustomers"
ACTION_COMMAND_ID = "logCallSummary"
CHOICE_LIMIT = 100
SUMMARY_LIMIT = 4000
PAGE_SIZE = 25
CONTACT_LIMIT = 20
CARD_BYTE_LIMIT = 16000


def plain_text(value):
    """Escape Markdown-sensitive characters before displaying text in a Teams card."""
    return re.sub(r"([\\`*_{}\[\]()#+.!<>|~-])", r"\\\1", value)


def customer_card(company):
    """Build an Adaptive Card with a customer's details and bounded contact list."""
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


def search_customers(text, skip, count):
    """Return a page of matching customers annotated with their contact counts."""
    return list(
        Company.objects.filter(name__icontains=text).annotate(
            contact_count=Count("contacts"),
        )[skip:skip + count]
    )


def selected_customer(pk):
    """Fetch one customer with a bounded set of contacts for its detail card."""
    return Company.objects.annotate(contact_count=Count("contacts")).prefetch_related(Prefetch(
        "contacts", queryset=Contact.objects.all()[:CONTACT_LIMIT], to_attr="card_contacts",
    )).filter(pk=pk).first()


def message_text(payload):
    """Extract plain text from a Teams message payload, bounded for use as a summary."""
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return ""
    content = body["content"]
    if body.get("contentType") == "html":
        content = html.unescape(strip_tags(re.sub(r"(?i)<br\s*/?>|</p>", "\n", content)))
    return content.strip()[:SUMMARY_LIMIT]


def task_response(card=None, title="Log call summary", message=None):
    """Build a Teams task-module response with a card, or a final message."""
    if message is not None:
        task = {"type": "message", "value": message}
    else:
        task = {"type": "continue", "value": {
            "title": title, "width": "medium", "height": "auto",
            "card": {"contentType": "application/vnd.microsoft.card.adaptive", "content": card},
        }}
    return InvokeResponse(status=200, body={"task": task})


def _choice_step(step, intro, input_id, label, choices, data, extra=()):
    submit = {"type": "Action.Submit", "title": "Next" if step != "call" else "Save summary",
              "data": {**data, "step": step}}
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.3",
        "body": [
            {"type": "TextBlock", "text": intro, "wrap": True},
            {"type": "Input.ChoiceSet", "id": input_id, "label": label, "style": "compact",
             "isRequired": True, "errorMessage": "Select an option.", "choices": choices},
            *extra,
        ],
        "actions": [submit],
    }


def customer_step(message):
    """First step: choose a customer."""
    companies = list(Company.objects.all()[:CHOICE_LIMIT])
    if not companies:
        return task_response(message="No customers are available.")
    return task_response(_choice_step(
        "customer", "Select the customer for this call.", "customer_id", "Customer",
        [{"title": c.name[:100], "value": str(c.pk)} for c in companies],
        {"message": message},
    ))


def contact_step(company, message):
    """Second step: choose one of the customer's contacts."""
    contacts = list(company.contacts.all()[:CHOICE_LIMIT])
    if not contacts:
        return task_response(message="This customer has no contacts.")
    return task_response(_choice_step(
        "contact", f"Select a contact at {company.name}.", "contact_id", "Contact",
        [{"title": f"{c.name} ({c.email})"[:100], "value": str(c.pk)} for c in contacts],
        {"message": message},
    ))


def call_step(agent, contact, message):
    """Third step: choose one of the agent's calls with the contact and complete its summary."""
    calls = list(Call.objects.filter(agent=agent, contact=contact)[:CHOICE_LIMIT])
    if not calls:
        return task_response(message="You have no calls with this contact.")
    summary = {"type": "Input.Text", "id": "summary", "label": "Summary", "isMultiline": True,
               "isRequired": True, "errorMessage": "Enter a summary.",
               "maxLength": SUMMARY_LIMIT, "value": message}
    return task_response(_choice_step(
        "call", f"Select the call with {contact.name} and complete its summary.",
        "call_id", "Call",
        [{"title": f"#{c.pk} — {c.get_status_display()}", "value": str(c.pk)} for c in calls],
        {"contact_id": str(contact.pk)}, extra=[summary],
    ))


def _pk(value):
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return None
    return pk if 0 < pk < 2**63 else None


def handle_action(agent, name, value):
    """Handle the call-summary action command's fetchTask/submitAction invokes."""
    if value.get("commandId") != ACTION_COMMAND_ID:
        return InvokeResponse(status=400)
    if name == "composeExtension/fetchTask":
        return customer_step(message_text(value.get("messagePayload")))
    data = value.get("data")
    if not isinstance(data, dict):
        return InvokeResponse(status=400)
    message = data.get("message")
    message = message[:SUMMARY_LIMIT] if isinstance(message, str) else ""
    step = data.get("step")
    if step == "customer":
        company = Company.objects.filter(pk=_pk(data.get("customer_id"))).first()
        return contact_step(company, message) if company else InvokeResponse(status=404)
    contact = Contact.objects.filter(pk=_pk(data.get("contact_id"))).first()
    if not contact:
        return InvokeResponse(status=404)
    if step == "contact":
        return call_step(agent, contact, message)
    if step == "call":
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_LIMIT:
            return InvokeResponse(status=400)
        call = Call.objects.filter(pk=_pk(data.get("call_id")), agent=agent, contact=contact).first()
        if not call:
            return InvokeResponse(status=404)
        summary = summary.strip()
        call.summary = f"{call.summary.rstrip()}\n\n{summary}" if call.summary.strip() else summary
        call.save(update_fields=["summary"])
        return task_response(message="Call summary saved.")
    return InvokeResponse(status=400)


def result_response(attachments):
    """Wrap card attachments in a Teams messaging-extension result response."""
    response = MessagingExtensionResponse(compose_extension=MessagingExtensionResult(
        type="result", attachment_layout="list", attachments=attachments,
    ))
    return InvokeResponse(status=200, body=response.serialize())


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
