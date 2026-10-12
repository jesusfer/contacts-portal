"""Metadata-only tracing for outbound identity and Graph requests."""
import logging
from functools import partial
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import requests


logger = logging.getLogger("crm.external_calls")


def _response_id(response, header):
    value = response.headers.get(header)
    try:
        return str(UUID(value)) if isinstance(value, str) else "-"
    except ValueError:
        return "-"


def logged_request(send, url, *, service, operation, method, **kwargs):
    call_id = uuid4().hex
    # Only the hostname is logged: URLs can contain credentials, codes or user IDs.
    host = urlsplit(url).hostname or "-"
    context = (
        f"call_id={call_id} service={service} operation={operation} "
        f"method={method.upper()} host={ascii(host)}"
    )
    started = monotonic()
    logger.info("external_call.start %s", context)
    try:
        response = send(url, **kwargs)
    except requests.RequestException as exc:
        logger.warning(
            "external_call.failed %s duration_ms=%.1f error_type=%s",
            context, (monotonic() - started) * 1000, type(exc).__name__,
        )
        raise
    log = logger.info if 200 <= response.status_code < 300 else logger.warning
    log(
        "external_call.complete %s status=%s duration_ms=%.1f "
        "request_id=%s correlation_id=%s",
        context, response.status_code, (monotonic() - started) * 1000,
        _response_id(response, "request-id"),
        _response_id(response, "client-request-id"),
    )
    return response


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
        partial(send, method), url, service="oidc", operation=operation,
        method=method, **kwargs,
    )
