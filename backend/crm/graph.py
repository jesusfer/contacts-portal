"""Delegated, read-only Microsoft Graph requests."""

from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import requests
from asgiref.sync import async_to_sync
from azure.core.exceptions import ClientAuthenticationError
from django.core import signing
from django.utils.html import strip_tags
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.response_handler import ResponseHandler
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from kiota_http.middleware.options import ResponseHandlerOption
from microsoft_teams.graph import AuthProvider
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter

from .external_calls import logged_request


class GraphClientError(Exception):
    status = 502
    public_message = "Microsoft Graph is temporarily unavailable. Try again later."


class TokenExpiredError(GraphClientError):
    status = 403
    public_message = "Microsoft Graph authorization required. Sign in again."


class InvalidCursor(ValueError):
    pass


class _GraphTransport(httpx.AsyncBaseTransport):
    """Keep SDK calls on the portal's bounded, metadata-only requests transport."""

    async def handle_async_request(self, request):
        url = urlsplit(str(request.url))
        if (
            request.method != "GET"
            or url.scheme != "https"
            or url.netloc != "graph.microsoft.com"
            or url.path not in {
                "/v1.0/me",
                "/v1.0/me/photo/$value",
                "/v1.0/me/calendarView",
                "/v1.0/me/mailFolders('inbox')/messages",
                "/v1.0/me/presence",
            }
            or url.fragment
        ):
            raise GraphClientError("Unsupported Graph request.")
        endpoint = url.path.removeprefix("/v1.0")
        response = logged_request(
            requests.get,
            f"https://graph.microsoft.com{url.path}",
            service="graph",
            operation=endpoint,
            method="GET",
            headers=requests.structures.CaseInsensitiveDict(request.headers),
            params=dict(parse_qsl(url.query)) or None,
            timeout=10,
            allow_redirects=False,
        )
        return httpx.Response(
            response.status_code,
            request=request,
            extensions={"portal_response": response},
        )


class _GraphResponseHandler(ResponseHandler):
    async def handle_response_async(self, response, error_map):
        # Preserve strict JSON validation and the existing public error contract.
        return response.extensions["portal_response"]


class GraphClient:
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    CURSOR_SALT = "crm.graph.pagination.v1"
    PROFILE_FIELDS = "id,displayName,givenName,surname,mail,userPrincipalName"

    def __init__(self, access_token, tenant_id=None, *, user_id=None, provider_id=None):
        """Create a Graph client bound to a delegated token and optional identity context."""
        self.access_token = access_token
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.provider_id = provider_id

    async def _sdk_request(self, endpoint, params):
        async with httpx.AsyncClient(
            transport=_GraphTransport(), timeout=10, follow_redirects=False
        ) as transport:
            adapter = GraphRequestAdapter(
                AzureIdentityAuthenticationProvider(AuthProvider(self.access_token)),
                client=transport,
            )
            graph = GraphServiceClient(request_adapter=adapter)
            builders = {
                "/me": graph.me,
                "/me/photo/$value": graph.me.photo.content,
                "/me/calendarView": graph.me.calendar_view,
                "/me/mailFolders('inbox')/messages": graph.me.mail_folders.by_mail_folder_id(
                    "inbox"
                ).messages,
                "/me/presence": graph.me.presence,
            }
            if endpoint not in builders:
                raise GraphClientError("Unsupported Graph request.")
            # with_url supports opaque skip tokens after our cursor validation.
            url = f"{self.GRAPH_BASE}{endpoint}"
            if params:
                url += "?" + urlencode(params)
            configuration = RequestConfiguration(
                options=[ResponseHandlerOption(_GraphResponseHandler())]
            )
            configuration.headers.add(
                "Prefer", 'outlook.timezone="UTC", outlook.body-content-type="text"'
            )
            return await builders[endpoint].with_url(url).get(configuration)

    def _request(self, endpoint, params=None, *, photo=False):
        """Use Graph SDK operations with the existing transport and error policy."""
        try:
            response = async_to_sync(self._sdk_request)(endpoint, params)
        except ClientAuthenticationError as exc:
            raise TokenExpiredError("Graph authorization required. Sign in again.") from exc
        except requests.RequestException as exc:
            raise GraphClientError("Graph service unavailable.") from exc
        if photo and response.status_code == 404:
            return response
        if response.status_code in (401, 403):
            raise TokenExpiredError("Graph access denied. Sign in again.")
        if not 200 <= response.status_code < 300:
            raise GraphClientError("Graph service returned an unsuccessful response.")
        return response

    def _json(self, endpoint, params=None):
        """Return an object response from Graph, rejecting invalid JSON payloads."""
        try:
            data = self._request(endpoint, params).json()
        except ValueError as exc:
            raise GraphClientError("Invalid Graph response.") from exc
        if not isinstance(data, dict):
            raise GraphClientError("Invalid Graph response.")
        return data

    def get_profile(self):
        """Fetch and validate the delegated user's basic Microsoft Graph profile."""
        data = self._json("/me", {"$select": self.PROFILE_FIELDS})
        if (
            not isinstance(data.get("id"), str)
            or not data["id"]
            or any(
                data.get(field) is not None and not isinstance(data[field], str)
                for field in self.PROFILE_FIELDS.split(",")
            )
        ):
            raise GraphClientError("Invalid Graph profile.")
        return data

    def get_photo(self):
        """Fetch the delegated user's profile photo, returning ``None`` when absent."""
        response = self._request("/me/photo/$value", photo=True)
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise TokenExpiredError("Graph access denied. Sign in again.")
        if response.status_code != 200:
            raise GraphClientError("Graph photo unavailable.")
        return response.content

    def _context(self, kind):
        """Build the identity context used to bind signed pagination cursors."""
        return {
            "kind": kind,
            "user": self.user_id,
            "provider": self.provider_id,
            "tenant": self.tenant_id,
        }

    def _page(self, kind, endpoint, params, page):
        """Fetch a Graph page and issue a signed cursor for a validated next link."""
        if page is not None:
            try:
                cursor = signing.loads(page, salt=self.CURSOR_SALT, max_age=3600)
                if not isinstance(cursor, dict) or set(cursor) != {"context", "params"}:
                    raise InvalidCursor
                if cursor["context"] != self._context(kind):
                    raise InvalidCursor
                paging = cursor["params"]
                if not isinstance(paging, dict) or not (
                    {"$skip", "$skiptoken"} & paging.keys()
                ):
                    raise InvalidCursor
                if set(paging) - (set(params) | {"$skip", "$skiptoken"}) or any(
                    not isinstance(value, str) or len(value) > 8192
                    for value in paging.values()
                ):
                    raise InvalidCursor
                # The signed cursor carries the original calendar range.
                params = paging
            except (signing.BadSignature, ValueError, TypeError) as exc:
                raise InvalidCursor("Invalid or expired page cursor.") from exc
        data = self._json(endpoint, params)
        values = data.get("value")
        if not isinstance(values, list) or any(
            not isinstance(item, dict) for item in values
        ):
            raise GraphClientError("Invalid Graph page.")
        next_page = None
        next_link = data.get("@odata.nextLink")
        if next_link:
            try:
                url = urlsplit(next_link)
                pairs = parse_qsl(
                    url.query, keep_blank_values=True, strict_parsing=True
                )
                query = dict(pairs)
                if (
                    url.scheme != "https"
                    or url.netloc != "graph.microsoft.com"
                    or url.path != f"/v1.0{endpoint}"
                    or url.fragment
                    or len(query) != len(pairs)
                    or set(query) - (set(params) | {"$skip", "$skiptoken"})
                    or not ({"$skip", "$skiptoken"} & query.keys())
                    or any(len(value) > 8192 for value in query.values())
                    or any(
                        query.get(key, value) != str(value)
                        for key, value in params.items()
                        if key not in {"$skip", "$skiptoken"}
                    )
                ):
                    raise ValueError
                query = {**{key: str(value) for key, value in params.items()}, **query}
            except (ValueError, TypeError, AttributeError) as exc:
                raise GraphClientError("Invalid Graph pagination link.") from exc
            next_page = signing.dumps(
                {"context": self._context(kind), "params": query},
                salt=self.CURSOR_SALT,
                compress=True,
            )
        return values, next_page

    @staticmethod
    def _event_time(value):
        """Normalize a Graph date-time value to a UTC ISO timestamp."""
        try:
            date = datetime.fromisoformat(value["dateTime"])
            if date.tzinfo is None:
                date = date.replace(tzinfo=ZoneInfo(value.get("timeZone", "UTC")))
            return date.astimezone(timezone.utc).isoformat()
        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            ZoneInfoNotFoundError,
        ) as exc:
            raise GraphClientError("Invalid Graph event time.") from exc

    def get_calendar_view(self, start_dt, end_dt, top=50, page=None):
        """Return calendar events and a signed cursor for the next Graph page."""
        params = {
            "startDateTime": start_dt.astimezone(timezone.utc).isoformat(),
            "endDateTime": end_dt.astimezone(timezone.utc).isoformat(),
            "$select": "id,subject,bodyPreview,start,end,location,webLink,onlineMeetingUrl,isOnlineMeeting",
            "$orderby": "start/dateTime asc",
            "$top": str(top),
        }
        values, next_page = self._page("calendar", "/me/calendarView", params, page)
        return {
            "events": [
                {
                    "id": event.get("id"),
                    "subject": event.get("subject", ""),
                    "body_preview": event.get("bodyPreview", ""),
                    "starts_at": self._event_time(event.get("start", {})),
                    "ends_at": self._event_time(event.get("end", {})),
                    "location": (event.get("location") or {}).get("displayName", ""),
                    "web_url": event.get("webLink"),
                    "online_meeting_url": event.get("onlineMeetingUrl"),
                }
                for event in values
            ],
            "next_page": next_page,
        }

    def get_mailbox_messages(self, top=25, page=None):
        """Return normalized inbox messages and a signed cursor for the next page."""
        params = {
            "$select": "id,subject,body,bodyPreview,from,receivedDateTime,isRead,webLink",
            "$orderby": "receivedDateTime desc",
            "$top": str(top),
        }
        values, next_page = self._page(
            "inbox", "/me/mailFolders('inbox')/messages", params, page
        )
        emails = []
        for message in values:
            sender = message.get("from") or {}
            body = message.get("body") or {}
            if not isinstance(sender, dict) or not isinstance(body, dict):
                raise GraphClientError("Invalid Graph message.")
            sender = sender.get("emailAddress") or {}
            text = body.get("content", message.get("bodyPreview") or "")
            if not isinstance(sender, dict) or not isinstance(text, str):
                raise GraphClientError("Invalid Graph message.")
            if body.get("contentType") == "html":
                text = strip_tags(text)
            emails.append(
                {
                    "id": message.get("id"),
                    "subject": message.get("subject", ""),
                    "contact": {
                        "name": sender.get("name", ""),
                        "email": sender.get("address", ""),
                    },
                    "body": text,
                    "received_at": message.get("receivedDateTime"),
                    "is_read": message.get("isRead", False),
                    "is_replied": False,
                    "web_url": message.get("webLink"),
                }
            )
        return {"emails": emails, "next_page": next_page}

    def get_presence(self):
        """Return the delegated user's current Microsoft Graph presence."""
        data = self._json("/me/presence")
        return {
            "availability": data.get("availability"),
            "activity": data.get("activity"),
        }
