"""Metadata-only tracing for outbound identity and Graph requests."""

import logging
from contextlib import asynccontextmanager
from functools import partial
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import requests


logger = logging.getLogger("crm.external_calls")


def _response_id(response, *headers):
    for header in headers:
        value = response.headers.get(header)
        try:
            if isinstance(value, str):
                return str(UUID(value))
        except ValueError:
            pass
    return "-"


def logged_request(send, url, *, service, operation, method, **kwargs):
    context, started = _start_call(url, service, operation, method)
    try:
        response = send(url, **kwargs)
    except requests.RequestException as exc:
        _failed_call(context, started, exc)
        raise
    _complete_call(context, started, response)
    return response


def _start_call(url, service, operation, method):
    call_id = uuid4().hex
    # Only the hostname is logged: URLs can contain credentials, codes or user IDs.
    host = urlsplit(url).hostname or "-"
    context = (
        f"call_id={call_id} service={service} operation={operation} "
        f"method={method.upper()} host={ascii(host)}"
    )
    started = monotonic()
    logger.info("external_call.start %s", context)
    return context, started


def _failed_call(context, started, exc):
    logger.warning(
        "external_call.failed %s duration_ms=%.1f error_type=%s",
        context,
        (monotonic() - started) * 1000,
        type(exc).__name__,
    )


def _complete_call(context, started, response):
    log = logger.info if 200 <= response.status_code < 300 else logger.warning
    log(
        "external_call.complete %s status=%s duration_ms=%.1f "
        "request_id=%s correlation_id=%s",
        context,
        response.status_code,
        (monotonic() - started) * 1000,
        _response_id(response, "request-id", "x-ms-request-id"),
        _response_id(response, "client-request-id", "x-ms-correlation-id"),
    )


@asynccontextmanager
async def graph_http_client():
    async def on_request(request):
        # The SDK's middleware resolves its /me placeholder after this hook.
        operation = request.url.path.removeprefix("/v1.0").replace(
            "/users/me-token-to-replace", "/me", 1
        )
        request.extensions["portal_trace"] = _start_call(
            str(request.url), "graph", operation, request.method,
        )

    async def on_response(response):
        _complete_call(*response.request.extensions["portal_trace"], response)
        if response.is_redirect:
            response.raise_for_status()

    async with httpx.AsyncClient(
        timeout=10,
        follow_redirects=False,
        event_hooks={"request": [on_request], "response": [on_response]},
    ) as client:
        try:
            yield client
        except httpx.RequestError as exc:
            _failed_call(*exc.request.extensions["portal_trace"], exc)
            raise


def oidc_request(send, method, url, **kwargs):
    path = urlsplit(url).path.rstrip("/")
    if path.endswith("/.well-known/openid-configuration"):
        operation = "discovery"
    elif path.endswith("/token"):
        operation = "token_exchange"
    elif path.endswith(("/keys", "/jwks", "/jwks.json")):
        operation = "signing_keys"
    elif path.endswith("/userinfo"):
        operation = "userinfo"
    else:
        operation = "http_request"
    return logged_request(
        partial(send, method),
        url,
        service="oidc",
        operation=operation,
        method=method,
        **kwargs,
    )
