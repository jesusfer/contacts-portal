"""Delegated, read-only Microsoft Graph requests."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from asgiref.sync import async_to_sync
from azure.core.exceptions import ClientAuthenticationError
from django.utils.html import strip_tags
from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from kiota_http.middleware.options import RedirectHandlerOption, RetryHandlerOption
from microsoft_teams.graph import AuthProvider
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter, options as graph_options
from msgraph.generated.users.item.calendar_view.calendar_view_request_builder import (
    CalendarViewRequestBuilder,
)
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder,
)
from msgraph.generated.users.item.user_item_request_builder import UserItemRequestBuilder
from msgraph_core import GraphClientFactory

from .external_calls import graph_http_client


class GraphClientError(Exception):
    status = 502
    public_message = "Microsoft Graph is temporarily unavailable. Try again later."


class TokenExpiredError(GraphClientError):
    status = 403
    public_message = "Microsoft Graph authorization required. Sign in again."


class GraphClient:
    PROFILE_FIELDS = (
        "id", "displayName", "givenName", "surname", "mail", "userPrincipalName",
    )
    ITEM_LIMIT = 5

    def __init__(self, access_token):
        self.access_token = access_token

    async def _sdk_request(self, operation, query):
        # Each synchronous entry point gets its own event loop and HTTP client.
        async with graph_http_client() as transport:
            graph = GraphServiceClient(
                request_adapter=GraphRequestAdapter(
                    AzureIdentityAuthenticationProvider(AuthProvider(self.access_token)),
                    client=GraphClientFactory.create_with_default_middleware(
                        client=transport,
                        options={
                            **graph_options,
                            RetryHandlerOption.get_key(): RetryHandlerOption(
                                max_retries=0
                            ),
                            RedirectHandlerOption.get_key(): RedirectHandlerOption(
                                should_redirect=False
                            ),
                        },
                    ),
                )
            )
            configuration = RequestConfiguration(query_parameters=query)
            configuration.headers.add(
                "Prefer", 'outlook.timezone="UTC", outlook.body-content-type="text"'
            )
            return await operation(graph, configuration)

    def _request(self, operation, query=None, *, photo=False):
        try:
            result = async_to_sync(self._sdk_request)(operation, query)
        except ClientAuthenticationError as exc:
            raise TokenExpiredError(
                "Graph authorization required. Sign in again."
            ) from exc
        except APIError as exc:
            if photo and exc.response_status_code == 404:
                return None
            if exc.response_status_code in (401, 403):
                raise TokenExpiredError("Graph access denied. Sign in again.") from exc
            raise GraphClientError(
                "Graph service returned an unsuccessful response."
            ) from exc
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise GraphClientError("Graph service unavailable.") from exc
        except (ValueError, TypeError) as exc:
            raise GraphClientError("Invalid Graph response.") from exc
        if result is None:
            raise GraphClientError("Empty Graph response.")
        return result

    def get_profile(self):
        profile = self._request(
            lambda graph, config: graph.me.get(config),
            UserItemRequestBuilder.UserItemRequestBuilderGetQueryParameters(
                select=list(self.PROFILE_FIELDS)
            ),
        )
        if not profile.id:
            raise GraphClientError("Missing Graph profile identity.")
        return {
            "id": profile.id,
            "displayName": profile.display_name,
            "givenName": profile.given_name,
            "surname": profile.surname,
            "mail": profile.mail,
            "userPrincipalName": profile.user_principal_name,
        }

    def get_photo(self):
        return self._request(
            lambda graph, config: graph.me.photo.content.get(config), photo=True
        )

    @staticmethod
    def _event_time(value):
        try:
            date = datetime.fromisoformat(value.date_time)
            if date.tzinfo is None:
                date = date.replace(tzinfo=ZoneInfo(value.time_zone or "UTC"))
            return date.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, AttributeError, ZoneInfoNotFoundError) as exc:
            raise GraphClientError("Invalid Graph event time.") from exc

    def get_calendar_view(self, start_dt, end_dt):
        data = self._request(
            lambda graph, config: graph.me.calendar_view.get(config),
            CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
                start_date_time=start_dt.astimezone(timezone.utc).isoformat(),
                end_date_time=end_dt.astimezone(timezone.utc).isoformat(),
                select=[
                    "id", "subject", "bodyPreview", "start", "end", "location",
                    "webLink", "onlineMeetingUrl",
                ],
                orderby=["start/dateTime asc"],
                top=self.ITEM_LIMIT,
            ),
        )
        if data.value is None:
            raise GraphClientError("Missing Graph calendar events.")
        return {
            "events": [
                {
                    "id": event.id,
                    "subject": event.subject or "",
                    "body_preview": event.body_preview or "",
                    "starts_at": self._event_time(event.start),
                    "ends_at": self._event_time(event.end),
                    "location": (
                        event.location.display_name or "" if event.location else ""
                    ),
                    "web_url": event.web_link,
                    "online_meeting_url": event.online_meeting_url,
                }
                for event in data.value[: self.ITEM_LIMIT]
            ],
        }

    def get_mailbox_messages(self):
        data = self._request(
            lambda graph, config: graph.me.mail_folders.by_mail_folder_id(
                "inbox"
            ).messages.get(config),
            MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
                select=[
                    "id", "subject", "body", "bodyPreview", "from",
                    "receivedDateTime", "isRead", "webLink",
                ],
                orderby=["receivedDateTime desc"],
                top=self.ITEM_LIMIT,
            ),
        )
        if data.value is None:
            raise GraphClientError("Missing Graph inbox messages.")
        emails = []
        for message in data.value[: self.ITEM_LIMIT]:
            sender = message.from_.email_address if message.from_ else None
            body = message.body
            text = (
                body.content
                if body and body.content is not None
                else message.body_preview or ""
            )
            if body and body.content_type and body.content_type.value.lower() == "html":
                text = strip_tags(text)
            emails.append(
                {
                    "id": message.id,
                    "subject": message.subject or "",
                    "contact": {
                        "name": sender.name or "" if sender else "",
                        "email": sender.address or "" if sender else "",
                    },
                    "body": text,
                    "received_at": (
                        message.received_date_time.isoformat()
                        if message.received_date_time else None
                    ),
                    "is_read": bool(message.is_read),
                    "is_replied": False,
                    "web_url": message.web_link,
                }
            )
        return {"emails": emails}

    def get_presence(self):
        data = self._request(lambda graph, config: graph.me.presence.get(config))
        return {"availability": data.availability, "activity": data.activity}
