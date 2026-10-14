"""Customer-search cards for the Teams messaging extension."""
import json

from asgiref.sync import sync_to_async
from botbuilder.core import CardFactory
from botbuilder.schema import CardAction, HeroCard, InvokeResponse
from botbuilder.schema.teams import (
    MessagingExtensionAttachment,
    MessagingExtensionResponse,
    MessagingExtensionResult,
)
from django.db.models import Count, Prefetch

from ...models import Company, Contact
from .text import plain_text


COMMAND_ID = "searchCustomers"
CONTACT_LIMIT = 20
PAGE_SIZE = 25
CARD_BYTE_LIMIT = 16000


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


def result_response(attachments):
    """Wrap card attachments in a Teams messaging-extension result response."""
    response = MessagingExtensionResponse(compose_extension=MessagingExtensionResult(
        type="result", attachment_layout="list", attachments=attachments,
    ))
    return InvokeResponse(status=200, body=response.serialize())


async def handle_search(activity):
    """Handle customer search and selected-customer invokes."""
    value = activity.value
    if activity.name == "composeExtension/selectItem":
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
