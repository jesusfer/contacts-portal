"""Customer-search cards for the Teams messaging extension."""

import json

from asgiref.sync import sync_to_async
from django.db.models import Count, Prefetch
from microsoft_teams.api import (
    Attachment,
    CardAction,
    HeroCard,
    InvokeResponse,
    MessagingExtensionAttachment,
    MessagingExtensionResponse,
    MessagingExtensionResult,
)
from microsoft_teams.cards import AdaptiveCard, Fact, FactSet, TextBlock

from ...models import Company, Contact
from .text import plain_text


COMMAND_ID = "searchCustomers"
CONTACT_LIMIT = 20
PAGE_SIZE = 25
CARD_BYTE_LIMIT = 16000


class SelectionAction(CardAction):
    """A hero-card tap does not need the SDK's required button title."""

    title: str | None = None


def serialize_card(card):
    """Serialize SDK cards with the Adaptive Cards schema's wire-format key."""
    payload = card.model_dump(mode="json", by_alias=True, exclude_none=True)
    # Teams SDK 2.1 serializes ac_schema as "schema", not "$schema".
    payload.pop("schema", None)
    payload["$schema"] = "http://adaptivecards.io/schemas/adaptive-card.json"
    return payload


def customer_card(company):
    """Build an Adaptive Card with a customer's details and bounded contact list."""
    body = [
        TextBlock(
            text=plain_text(company.name), weight="Bolder", size="Large", wrap=True
        ),
        FactSet(
            facts=[
                Fact(title="Customer ID", value=str(company.pk)),
                Fact(title="Contacts", value=str(company.contact_count)),
            ],
        ),
    ]
    card = AdaptiveCard(version="1.3", body=body)
    body = card.body
    shown = 0
    for contact in company.card_contacts:
        block = TextBlock(
            text=f"{plain_text(contact.name)}\n\n{plain_text(contact.email)}",
            wrap=True,
            separator=True,
        )
        body.append(block)
        if len(json.dumps(serialize_card(card)).encode("utf-8")) > CARD_BYTE_LIMIT - 1024:
            body.pop()
            break
        shown += 1
    if not company.contact_count:
        body.append(TextBlock(text="No contacts recorded.", wrap=True))
    elif shown < company.contact_count:
        body.append(
            TextBlock(
                text=(
                    f"Showing {shown} of {company.contact_count} contacts. Open the portal for the full directory."
                ),
                wrap=True,
            ),
        )
    return serialize_card(card)


def search_customers(agent, text, skip, count):
    """Return a page of matching customers annotated with their contact counts."""
    return list(
        agent.companies.filter(name__icontains=text).annotate(
            contact_count=Count("contacts"),
        )[skip : skip + count]
    )


def selected_customer(agent, pk):
    """Fetch one customer with a bounded set of contacts for its detail card."""
    return (
        agent.companies.annotate(contact_count=Count("contacts"))
        .prefetch_related(
            Prefetch(
                "contacts",
                queryset=Contact.objects.all()[:CONTACT_LIMIT],
                to_attr="card_contacts",
            )
        )
        .filter(pk=pk)
        .first()
    )


def result_response(attachments):
    """Wrap card attachments in a Teams messaging-extension result response."""
    response = MessagingExtensionResponse(
        compose_extension=MessagingExtensionResult(
            type="result",
            attachment_layout="list",
            attachments=attachments,
        )
    )
    return InvokeResponse[dict](
        status=200,
        body=response.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


async def handle_search(agent, activity):
    """Handle customer search and selected-customer invokes."""
    value = activity.value
    if activity.name == "composeExtension/selectItem":
        pk = value.get("customer_id") if isinstance(value, dict) else None
        if type(pk) is not int or not 0 < pk < 2**63:
            return InvokeResponse(status=400)
        company = await sync_to_async(selected_customer)(agent, pk)
        if not company:
            return InvokeResponse(status=404)
        return result_response(
            [
                MessagingExtensionAttachment(
                    content_type="application/vnd.microsoft.card.adaptive",
                    content=customer_card(company),
                )
            ]
        )
    if (
        not isinstance(value, dict)
        or value.get("commandId") != COMMAND_ID
        or not isinstance(value.get("parameters"), list)
    ):
        return InvokeResponse(status=400)
    parameters = value["parameters"]
    if (
        len(parameters) != 1
        or not isinstance(parameters[0], dict)
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
        type(skip) is not int
        or not 0 <= skip <= 10000
        or type(count) is not int
        or not 1 <= count <= PAGE_SIZE
    ):
        return InvokeResponse(status=400)
    companies = await sync_to_async(search_customers)(
        agent,
        parameters[0]["value"].strip(),
        skip,
        count,
    )
    attachments = []
    for company in companies:
        preview = Attachment(
            content_type="application/vnd.microsoft.card.hero",
            content=HeroCard(
                title=plain_text(
                    company.name[:80] + ("…" if len(company.name) > 80 else "")
                ),
                subtitle=f"{company.contact_count} contacts",
                tap=SelectionAction(type="invoke", value={"customer_id": company.pk}),
            )
        )
        attachments.append(
            MessagingExtensionAttachment(
                content_type=preview.content_type,
                content=preview.content,
                preview=preview,
            )
        )
    return result_response(attachments)
