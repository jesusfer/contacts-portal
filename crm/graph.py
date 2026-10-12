"""Delegated, read-only Microsoft Graph requests."""
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from django.core import signing
from django.utils.html import strip_tags

from .external_calls import logged_request


class GraphClientError(Exception):
    status = 502
    public_message = "Microsoft Graph is temporarily unavailable. Try again later."


class TokenExpiredError(GraphClientError):
    status = 403
    public_message = "Microsoft Graph authorization required. Sign in again."


class InvalidCursor(ValueError):
    pass


class GraphClient:
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    CURSOR_SALT = "crm.graph.pagination.v1"
    PROFILE_FIELDS = "id,displayName,givenName,surname,mail,userPrincipalName"

    def __init__(self, access_token, tenant_id=None, *, user_id=None, provider_id=None):
        self.access_token = access_token
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.provider_id = provider_id

    def _request(self, endpoint, params=None):
        try:
            response = logged_request(
                requests.get,
                f"{self.GRAPH_BASE}{endpoint}",
                service="graph",
                operation=endpoint if endpoint in {
                    "/me", "/me/calendarView", "/me/mailFolders/inbox/messages", "/me/presence",
                } else "http_request",
                method="GET",
                headers={
                    "Authorization": "Bearer " + self.access_token,
                    "Accept": "application/json",
                    "Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"',
                },
                params=params, timeout=10, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GraphClientError("Graph service unavailable.") from exc
        if response.status_code in (401, 403):
            raise TokenExpiredError("Graph access denied. Sign in again.")
        if not 200 <= response.status_code < 300:
            raise GraphClientError("Graph service returned an unsuccessful response.")
        return response

    def _json(self, endpoint, params=None):
        try:
            data = self._request(endpoint, params).json()
        except ValueError as exc:
            raise GraphClientError("Invalid Graph response.") from exc
        if not isinstance(data, dict):
            raise GraphClientError("Invalid Graph response.")
        return data

    def get_profile(self):
        data = self._json("/me", {"$select": self.PROFILE_FIELDS})
        if not isinstance(data.get("id"), str) or not data["id"] or any(
            data.get(field) is not None and not isinstance(data[field], str)
            for field in self.PROFILE_FIELDS.split(",")
        ):
            raise GraphClientError("Invalid Graph profile.")
        return data

    def get_photo(self):
        try:
            response = logged_request(
                requests.get,
                f"{self.GRAPH_BASE}/me/photo/$value",
                service="graph", operation="/me/photo/$value", method="GET",
                headers={"Authorization": "Bearer " + self.access_token},
                timeout=10, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GraphClientError("Graph photo unavailable.") from exc
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise TokenExpiredError("Graph access denied. Sign in again.")
        if response.status_code != 200:
            raise GraphClientError("Graph photo unavailable.")
        return response.content

    def _context(self, kind):
        return {
            "kind": kind, "user": self.user_id,
            "provider": self.provider_id, "tenant": self.tenant_id,
        }

    def _page(self, kind, endpoint, params, page):
        if page is not None:
            try:
                cursor = signing.loads(page, salt=self.CURSOR_SALT, max_age=3600)
                if not isinstance(cursor, dict) or set(cursor) != {"context", "params"}:
                    raise InvalidCursor
                if cursor["context"] != self._context(kind):
                    raise InvalidCursor
                paging = cursor["params"]
                if not isinstance(paging, dict) or not ({"$skip", "$skiptoken"} & paging.keys()):
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
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise GraphClientError("Invalid Graph page.")
        next_page = None
        next_link = data.get("@odata.nextLink")
        if next_link:
            try:
                url = urlsplit(next_link)
                pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
                query = dict(pairs)
                if (
                    url.scheme != "https" or url.netloc != "graph.microsoft.com"
                    or url.path != f"/v1.0{endpoint}" or url.fragment
                    or len(query) != len(pairs)
                    or set(query) - (set(params) | {"$skip", "$skiptoken"})
                    or not ({"$skip", "$skiptoken"} & query.keys())
                    or any(len(value) > 8192 for value in query.values())
                    or any(query.get(key, value) != str(value) for key, value in params.items()
                           if key not in {"$skip", "$skiptoken"})
                ):
                    raise ValueError
                query = {**{key: str(value) for key, value in params.items()}, **query}
            except (ValueError, TypeError, AttributeError) as exc:
                raise GraphClientError("Invalid Graph pagination link.") from exc
            next_page = signing.dumps(
                {"context": self._context(kind), "params": query},
                salt=self.CURSOR_SALT, compress=True,
            )
        return values, next_page

    @staticmethod
    def _event_time(value):
        try:
            date = datetime.fromisoformat(value["dateTime"])
            if date.tzinfo is None:
                date = date.replace(tzinfo=ZoneInfo(value.get("timeZone", "UTC")))
            return date.astimezone(timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError, AttributeError, ZoneInfoNotFoundError) as exc:
            raise GraphClientError("Invalid Graph event time.") from exc

    def get_calendar_view(self, start_dt, end_dt, top=50, page=None):
        params = {
            "startDateTime": start_dt.astimezone(timezone.utc).isoformat(),
            "endDateTime": end_dt.astimezone(timezone.utc).isoformat(),
            "$select": "id,subject,bodyPreview,start,end,location,webLink,onlineMeetingUrl,isOnlineMeeting",
            "$orderby": "start/dateTime asc", "$top": str(top),
        }
        values, next_page = self._page("calendar", "/me/calendarView", params, page)
        return {
            "events": [{
                "id": event.get("id"), "subject": event.get("subject", ""),
                "body_preview": event.get("bodyPreview", ""),
                "starts_at": self._event_time(event.get("start", {})),
                "ends_at": self._event_time(event.get("end", {})),
                "location": (event.get("location") or {}).get("displayName", ""),
                "web_url": event.get("webLink"),
                "online_meeting_url": event.get("onlineMeetingUrl"),
            } for event in values],
            "next_page": next_page,
        }

    def get_mailbox_messages(self, top=25, page=None):
        params = {
            "$select": "id,subject,body,bodyPreview,from,receivedDateTime,isRead,webLink",
            "$orderby": "receivedDateTime desc", "$top": str(top),
        }
        values, next_page = self._page("inbox", "/me/mailFolders/inbox/messages", params, page)
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
            emails.append({
                "id": message.get("id"), "subject": message.get("subject", ""),
                "contact": {"name": sender.get("name", ""), "email": sender.get("address", "")},
                "body": text,
                "received_at": message.get("receivedDateTime"),
                "is_read": message.get("isRead", False), "is_replied": False,
                "web_url": message.get("webLink"),
            })
        return {"emails": emails, "next_page": next_page}

    def get_presence(self):
        data = self._json("/me/presence")
        return {"availability": data.get("availability"), "activity": data.get("activity")}
