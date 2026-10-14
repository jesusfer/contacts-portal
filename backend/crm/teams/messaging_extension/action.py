"""Call-summary action for the Teams messaging extension."""

import html
import re

from django.utils.html import strip_tags
from microsoft_teams.api import (
    Attachment,
    CardTaskModuleTaskInfo,
    InvokeResponse,
    MessagingExtensionActionResponse,
    TaskModuleContinueResponse,
    TaskModuleMessageResponse,
)
from microsoft_teams.cards import (
    AdaptiveCard,
    Choice,
    ChoiceSetInput,
    SubmitAction,
    TextBlock,
    TextInput,
)

from ...models import Call, Contact
from .search import serialize_card


ACTION_COMMAND_ID = "logCallSummary"
CHOICE_LIMIT = 100
SUMMARY_LIMIT = 4000


def message_text(payload):
    """Extract plain text from a Teams message payload, bounded for use as a summary."""
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return ""
    content = body["content"]
    if body.get("contentType") == "html":
        content = html.unescape(
            strip_tags(re.sub(r"(?i)<br\s*/?>|</p>", "\n", content))
        )
    return content.strip()[:SUMMARY_LIMIT]


def task_response(card=None, title="Log call summary", message=None):
    """Build a Teams task-module response with a card, or a final message."""
    if message is not None:
        task = TaskModuleMessageResponse(value=message)
    else:
        task = TaskModuleContinueResponse(
            value=CardTaskModuleTaskInfo(
                title=title,
                width="medium",
                height="medium",
                card=Attachment(
                    content_type="application/vnd.microsoft.card.adaptive",
                    content=serialize_card(card),
                ),
            )
        )
    return InvokeResponse[dict](
        status=200,
        body=MessagingExtensionActionResponse(task=task).model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
    )


def _choice_step(step, intro, input_id, label, choices, data, extra=()):
    return AdaptiveCard(
        version="1.3",
        body=[
            TextBlock(text=intro, wrap=True),
            ChoiceSetInput(
                id=input_id,
                label=label,
                style="compact",
                is_required=True,
                error_message="Select an option.",
                choices=choices,
            ),
            *extra,
        ],
        actions=[SubmitAction(title="Next", data={**data, "step": step})],
    )


def customer_step(agent, message):
    """First step: choose a customer."""
    companies = list(agent.companies.all()[:CHOICE_LIMIT])
    if not companies:
        return task_response(message="No customers are available.")
    return task_response(
        _choice_step(
            "customer",
            "Select the customer for this call.",
            "customer_id",
            "Customer",
            [Choice(title=c.name[:100], value=str(c.pk)) for c in companies],
            {"message": message},
        )
    )


def contact_step(company, message):
    """Second step: choose one of the customer's contacts."""
    contacts = list(company.contacts.all()[:CHOICE_LIMIT])
    if not contacts:
        return task_response(message="This customer has no contacts.")
    return task_response(
        _choice_step(
            "contact",
            f"Select a contact at {company.name}.",
            "contact_id",
            "Contact",
            [
                Choice(title=f"{c.name} ({c.email})"[:100], value=str(c.pk))
                for c in contacts
            ],
            {"message": message},
        )
    )


def call_step(agent, contact, message):
    """Third step: choose one of the agent's calls with the contact."""
    calls = list(Call.objects.filter(agent=agent, contact=contact)[:CHOICE_LIMIT])
    if not calls:
        return task_response(message="You have no calls with this contact.")
    return task_response(
        _choice_step(
            "call",
            f"Select the call with {contact.name}.",
            "call_id",
            "Call",
            [
                Choice(title=f"#{c.pk} — {c.get_status_display()}", value=str(c.pk))
                for c in calls
            ],
            {"contact_id": str(contact.pk), "message": message},
        )
    )


def summary_step(call, message):
    """Final step: edit the selected call's current summary."""
    summary = call.summary
    if message:
        summary = f"{summary.rstrip()}\n\n{message}" if summary.strip() else message
    return task_response(
        AdaptiveCard(
            version="1.3",
            body=[
                TextBlock(text=f"Edit the summary for call #{call.pk}.", wrap=True),
                TextInput(
                    id="summary",
                    label="Summary",
                    is_multiline=True,
                    is_required=True,
                    error_message="Enter a summary.",
                    max_length=SUMMARY_LIMIT,
                    value=summary,
                ),
            ],
            actions=[
                SubmitAction(
                    title="Save summary",
                    data={
                        "step": "summary",
                        "contact_id": str(call.contact_id),
                        "call_id": str(call.pk),
                    },
                )
            ],
        )
    )


def _pk(value):
    try:
        pk = int(value)
    except (TypeError, ValueError):
        return None
    return pk if 0 < pk < 2**63 else None


def handle_action(agent, name, value):
    """Handle the call-summary action command's fetchTask/submitAction invokes."""
    if not isinstance(value, dict) or value.get("commandId") != ACTION_COMMAND_ID:
        return InvokeResponse(status=400)
    if name == "composeExtension/fetchTask":
        return customer_step(agent, message_text(value.get("messagePayload")))
    data = value.get("data")
    if not isinstance(data, dict):
        return InvokeResponse(status=400)
    message = data.get("message")
    if not isinstance(message, str) or not message:
        message = message_text(value.get("messagePayload"))
    message = message[:SUMMARY_LIMIT]
    step = data.get("step")
    if step == "customer":
        company = agent.companies.filter(pk=_pk(data.get("customer_id"))).first()
        return contact_step(company, message) if company else InvokeResponse(status=404)
    contact = Contact.objects.filter(
        pk=_pk(data.get("contact_id")), company__in=agent.companies.all()
    ).first()
    if not contact:
        return InvokeResponse(status=404)
    if step == "contact":
        return call_step(agent, contact, message)
    if step in ("call", "summary"):
        call = Call.objects.filter(
            pk=_pk(data.get("call_id")), agent=agent, contact=contact
        ).first()
        if not call:
            return InvokeResponse(status=404)
        if step == "call":
            return summary_step(call, message)
        summary = data.get("summary")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > SUMMARY_LIMIT
        ):
            return InvokeResponse(status=400)
        call.summary = summary.strip()
        call.save(update_fields=["summary"])
        return task_response(message="Call summary saved.")
    return InvokeResponse(status=400)
