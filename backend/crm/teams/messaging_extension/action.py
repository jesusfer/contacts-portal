"""Call-summary action for the Teams messaging extension."""

import html
import re

from botbuilder.schema import InvokeResponse
from django.utils.html import strip_tags

from ...models import Call, Contact


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
        task = {"type": "message", "value": message}
    else:
        task = {
            "type": "continue",
            "value": {
                "title": title,
                "width": "medium",
                "height": "medium",
                "card": {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                },
            },
        }
    return InvokeResponse(status=200, body={"task": task})


def _choice_step(step, intro, input_id, label, choices, data, extra=()):
    submit = {
        "type": "Action.Submit",
        "title": "Next" if step != "call" else "Save summary",
        "data": {**data, "step": step},
    }
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.3",
        "body": [
            {"type": "TextBlock", "text": intro, "wrap": True},
            {
                "type": "Input.ChoiceSet",
                "id": input_id,
                "label": label,
                "style": "compact",
                "isRequired": True,
                "errorMessage": "Select an option.",
                "choices": choices,
            },
            *extra,
        ],
        "actions": [submit],
    }


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
            [{"title": c.name[:100], "value": str(c.pk)} for c in companies],
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
                {"title": f"{c.name} ({c.email})"[:100], "value": str(c.pk)}
                for c in contacts
            ],
            {"message": message},
        )
    )


def call_step(agent, contact, message):
    """Third step: choose one of the agent's calls with the contact and complete its summary."""
    calls = list(Call.objects.filter(agent=agent, contact=contact)[:CHOICE_LIMIT])
    if not calls:
        return task_response(message="You have no calls with this contact.")
    summary = {
        "type": "Input.Text",
        "id": "summary",
        "label": "Summary",
        "isMultiline": True,
        "isRequired": True,
        "errorMessage": "Enter a summary.",
        "maxLength": SUMMARY_LIMIT,
        "value": message,
    }
    return task_response(
        _choice_step(
            "call",
            f"Select the call with {contact.name} and complete its summary.",
            "call_id",
            "Call",
            [
                {"title": f"#{c.pk} — {c.get_status_display()}", "value": str(c.pk)}
                for c in calls
            ],
            {"contact_id": str(contact.pk)},
            extra=[summary],
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
    message = message[:SUMMARY_LIMIT] if isinstance(message, str) else ""
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
    if step == "call":
        summary = data.get("summary")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > SUMMARY_LIMIT
        ):
            return InvokeResponse(status=400)
        call = Call.objects.filter(
            pk=_pk(data.get("call_id")), agent=agent, contact=contact
        ).first()
        if not call:
            return InvokeResponse(status=404)
        summary = summary.strip()
        call.summary = (
            f"{call.summary.rstrip()}\n\n{summary}" if call.summary.strip() else summary
        )
        call.save(update_fields=["summary"])
        return task_response(message="Call summary saved.")
    return InvokeResponse(status=400)
