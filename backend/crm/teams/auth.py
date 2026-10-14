"""Tenant-bound Teams authentication and server-side Graph credentials."""

import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import msal
import requests
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from ..external_calls import logged_request
from ..graph import GraphClient, GraphClientError, TokenExpiredError


class TeamsConfigError(ValueError):
    """Raised when an OIDC provider has invalid Teams or Graph configuration."""

    pass


class TeamsTokenError(ValueError):
    """Raised when Teams credentials or identity claims cannot be trusted."""

    public_message = "Teams sign-in failed. Sign in via OIDC first, then retry."


def graph_provider(provider_id):
    """Return the configured Entra ID OIDC app matching ``provider_id``, if any."""
    return next(
        (
            app
            for app in settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get(
                "APPS", []
            )
            if app["provider_id"] == provider_id
            and app["provider_id"].lower().startswith("entraid")
        ),
        None,
    )


def extract_tenant_from_url(server_url):
    """Extract a tenant ID from a supported Microsoft identity issuer URL."""
    try:
        url = urlsplit(server_url)
        parts = url.path.rstrip("/").split("/")
        if (
            url.scheme == "https"
            and url.netloc == "login.microsoftonline.com"
            and (len(parts) == 2 or (len(parts) == 3 and parts[2] == "v2.0"))
        ):
            if parts[1] == "organizations":
                return "organizations"
            return str(UUID(parts[1]))
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def provider_tenant(app, tenant_id=None):
    """Resolve and validate the tenant ID associated with an OIDC app.

    Organization-wide issuers require a verified tenant ID. Tenant-specific
    issuers only accept that same tenant ID when one is supplied.
    """
    issuer_tenant = extract_tenant_from_url(app["settings"].get("server_url", ""))
    if (
        not issuer_tenant
        or app["settings"].get("graph_tenant_id", issuer_tenant) != issuer_tenant
    ):
        raise TeamsConfigError("Invalid Graph tenant configuration.")
    if issuer_tenant == "organizations":
        try:
            return str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise TeamsConfigError(
                "A verified tenant ID is required for this provider."
            ) from exc
    if tenant_id is not None:
        try:
            tenant_id = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise TeamsConfigError("Invalid Graph tenant configuration.") from exc
        if tenant_id != issuer_tenant:
            raise TeamsConfigError("Invalid Graph tenant configuration.")
    return issuer_tenant


def load_teams_config(provider_settings):
    """Return valid Teams settings from an OIDC provider configuration.

    Raises:
        TeamsConfigError: If required settings are missing or the tenant differs
            from the provider's issuer tenant.
    """
    config = provider_settings.get("teams")
    tenant = extract_tenant_from_url(provider_settings.get("server_url", ""))
    if (
        not isinstance(config, dict)
        or not all(
            config.get(key)
            for key in ("tenant_id", "audience", "resource", "authorized_client_ids")
        )
        or config["tenant_id"] != tenant
    ):
        raise TeamsConfigError("Invalid Teams configuration.")
    return config


def _get_jwks(tenant_id, *, reload=False):
    """Load a tenant's signing keys, using the cache unless ``reload`` is set."""
    key = f"teams_jwks_{tenant_id}"
    keys = None if reload else cache.get(key)
    if keys is None:
        try:
            response = logged_request(
                requests.get,
                f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
                service="entraid",
                operation="signing_keys",
                method="GET",
                timeout=10,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise TeamsTokenError("Teams signing keys unavailable.")
            keys = response.json()
            if not isinstance(keys, dict) or not isinstance(keys.get("keys"), list):
                raise TeamsTokenError("Invalid Teams signing keys.")
        except (requests.RequestException, ValueError) as exc:
            raise TeamsTokenError("Teams signing keys unavailable.") from exc
        cache.set(key, keys, 3600)
    return keys


def validate_teams_token(token, config):
    """Verify a Teams SSO JWT and return its validated claims.

    Signature, issuer, audience, tenant, authorized client, timestamps, and
    delegated ``access_as_user`` scope are checked before claims are returned.

    Raises:
        TeamsTokenError: If the token, claims, or signing keys are invalid.
    """
    required = ["exp", "nbf", "iat", "iss", "aud", "tid", "oid", "sub", "scp", "azp"]
    try:
        header = jwt.get_unverified_header(token)
        if (
            header.get("alg") != "RS256"
            or not isinstance(header.get("kid"), str)
            or not header["kid"]
        ):
            raise TeamsTokenError("Invalid Teams signing algorithm or key.")
        unverified = jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False}
        )
        tenant_id = unverified.get("tid")
        if not isinstance(tenant_id, str):
            raise TeamsTokenError("Invalid Teams tenant.")
        tenant_id = str(UUID(tenant_id))
        if config["tenant_id"] != "organizations" and tenant_id != config["tenant_id"]:
            raise TeamsTokenError("Invalid Teams tenant.")
        key = None
        for reload in (False, True):
            for candidate in _get_jwks(tenant_id, reload=reload)["keys"]:
                if candidate.get("kid") == header["kid"]:
                    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(candidate))
                    break
            if key is not None:
                break
        if key is None:
            raise TeamsTokenError("Unknown Teams signing key.")
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=config["audience"],
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            options={"require": required},
        )
        if any(type(claims[key]) not in (int, float) for key in ("exp", "nbf", "iat")):
            raise TeamsTokenError("Invalid Teams token timestamps.")
        if (
            claims["tid"] != tenant_id
            or claims["aud"] != config["audience"]
            or any(
                not isinstance(claims[key], str) or not claims[key]
                for key in ("oid", "sub", "azp")
            )
            or claims["azp"] not in config["authorized_client_ids"]
            or not isinstance(claims["scp"], str)
            or "access_as_user" not in claims["scp"].split()
        ):
            raise TeamsTokenError("Invalid Teams identity, client, or scope.")
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise TeamsTokenError("Invalid Teams token.") from exc


def account_identity(extra_data):
    """Return the Entra object and tenant IDs from social-account token data."""
    claims = extra_data.get("id_token") if isinstance(extra_data, dict) else None
    if not isinstance(claims, dict):
        claims = extra_data if isinstance(extra_data, dict) else {}
    return claims.get("oid"), claims.get("tid")


def get_teams_account(provider_id, claims):
    """Find the unique active social account matching validated Teams claims.

    Raises:
        TeamsTokenError: If no unique active account is linked to the identity.
    """
    matches = [
        account
        for account in SocialAccount.objects.filter(
            provider=provider_id
        ).select_related("user")
        if account_identity(account.extra_data) == (claims["oid"], claims["tid"])
    ]
    if len(matches) != 1 or not matches[0].user.is_active:
        raise TeamsTokenError(
            "Teams identity is not linked to an active account. Sign in via OIDC first."
        )
    return matches[0]


GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
AUTHORIZATION_ERRORS = {
    "invalid_grant",
    "interaction_required",
    "consent_required",
    "login_required",
    "access_denied",
}


class MSALHTTPClient:
    """MSAL transport with bounded, non-redirecting, metadata-only tracing."""

    def __init__(self, operation):
        self.operation = operation

    def _request(self, method, url, **kwargs):
        kwargs.update(timeout=10, allow_redirects=False)
        response = logged_request(
            getattr(requests, method.lower()),
            url,
            service="entraid",
            operation=self.operation if method == "POST" else "discovery",
            method=method,
            **kwargs,
        )
        if response.status_code != 200:
            # Only OAuth error responses are safe for MSAL to interpret.
            if method != "POST" or response.status_code not in (400, 401, 403):
                raise GraphClientError("Graph token service unavailable.")
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, TypeError) as exc:
            # MSAL logs raw non-JSON response bodies; reject them at the boundary.
            raise GraphClientError("Invalid Graph token service response.") from exc
        if method == "POST" and "error" in data:
            if data["error"] in AUTHORIZATION_ERRORS:
                raise TokenExpiredError("Graph authorization required. Sign in again.")
            raise GraphClientError("Graph token service unavailable.")
        if response.status_code != 200:
            raise GraphClientError("Invalid Graph token service response.")
        return response

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)


def exchange_graph_token(
    app, *, assertion=None, refresh_token=None, tenant_id=None, token_cache=None
):
    """Acquire delegated credentials, importing legacy refresh tokens into MSAL."""
    tenant = provider_tenant(app, tenant_id)
    token_cache = token_cache if token_cache is not None else msal.SerializableTokenCache()
    try:
        client = msal.ConfidentialClientApplication(
            app["client_id"],
            client_credential=app["secret"],
            authority=f"https://login.microsoftonline.com/{tenant}",
            token_cache=token_cache,
            http_client=MSALHTTPClient(
                "on_behalf_of" if assertion is not None else "refresh_token"
            ),
            instance_discovery=False,
            enable_pii_log=False,
        )
        if assertion is not None:
            token = client.acquire_token_on_behalf_of(assertion, scopes=GRAPH_SCOPES)
        elif refresh_token:
            token = client.acquire_token_by_refresh_token(
                refresh_token, scopes=GRAPH_SCOPES
            )
        else:
            accounts = client.get_accounts()
            if len(accounts) == 1:
                token = client.acquire_token_silent_with_error(
                    GRAPH_SCOPES, account=accounts[0], force_refresh=True
                )
            elif not accounts:
                # OBO responses need not contain client_info / an MSAL account.
                # The session-bound cache still owns its refresh credential.
                credentials = list(
                    token_cache.search(
                        msal.TokenCache.CredentialType.REFRESH_TOKEN,
                        query={
                            "client_id": app["client_id"],
                            "environment": "login.microsoftonline.com",
                        },
                    )
                )
                token = (
                    client.acquire_token_by_refresh_token(
                        credentials[0]["secret"], scopes=GRAPH_SCOPES
                    )
                    if len(credentials) == 1
                    else None
                )
            else:
                token = None
        if token is None and assertion is None and not refresh_token:
            raise TokenExpiredError("Graph authorization required. Sign in again.")
        if isinstance(token, dict) and token.get("error") in AUTHORIZATION_ERRORS:
            raise TokenExpiredError("Graph authorization required. Sign in again.")
        if (
            not isinstance(token, dict)
            or "error" in token
            or not isinstance(token.get("access_token"), str)
            or not token["access_token"]
            or not isinstance(token.get("token_type"), str)
            or token["token_type"].lower() != "bearer"
            or type(token.get("expires_in")) is not int
            or not 0 < token["expires_in"] <= 86400
            or (
                "refresh_token" in token and not isinstance(token["refresh_token"], str)
            )
        ):
            raise GraphClientError("Invalid Graph token response.")
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise GraphClientError("Graph token service unavailable.") from exc
    return token


class GraphTokenManager:
    """Store and refresh delegated Graph tokens in a user's server-side session."""

    SESSION_KEY = "graph_tokens"

    @classmethod
    def store_token(
        cls,
        request,
        provider_id,
        access_token,
        refresh_token=None,
        expires_in=3600,
        tenant_id=None,
        *,
        expires_at=None,
        oid=None,
        token_cache=None,
    ):
        """Save one provider's token data and mark Graph access as enabled."""
        if settings.SESSION_ENGINE == "django.contrib.sessions.backends.signed_cookies":
            raise TeamsConfigError("Graph credentials require server-side sessions.")
        expiry = expires_at or timezone.now() + timedelta(seconds=expires_in)
        app = graph_provider(provider_id)
        user_id = getattr(getattr(request, "user", None), "pk", None)
        user_id = user_id or request.session.get("_auth_user_id")
        request.session[cls.SESSION_KEY] = {
            provider_id: {
                "access_token": access_token,
                "refresh_token": refresh_token or "",
                "expires_at": expiry.isoformat(),
                "tenant_id": tenant_id,
                "oid": oid,
                "user_id": str(user_id) if user_id is not None else None,
                "provider_id": provider_id,
                "client_id": app["client_id"] if app else None,
                "msal_cache": token_cache.serialize() if token_cache is not None else None,
            }
        }
        request.session["graph_enabled"] = True

    @classmethod
    def get_token(cls, request, provider_id):
        """Return a usable access token, refreshing it when it is near expiry.

        A refreshed token is checked against the stored Entra object ID when
        available. Invalid or expired authorization raises ``TokenExpiredError``.
        """
        app = graph_provider(provider_id)
        token = request.session.get(cls.SESSION_KEY, {}).get(provider_id)
        if not app or not isinstance(token, dict):
            raise TokenExpiredError("Graph authorization required. Sign in again.")
        try:
            if settings.SESSION_ENGINE == "django.contrib.sessions.backends.signed_cookies":
                raise ValueError
            tenant = provider_tenant(app, token.get("tenant_id"))
            expiry = datetime.fromisoformat(token["expires_at"])
            if timezone.is_naive(expiry) or token.get("tenant_id") != tenant:
                raise ValueError
            user_id = getattr(getattr(request, "user", None), "pk", None)
            if (
                not user_id
                or token.get("user_id") != str(user_id)
                or token.get("provider_id") != provider_id
                or token.get("client_id") != app["client_id"]
                or not isinstance(token.get("oid"), str)
                or not token["oid"]
            ):
                raise ValueError
            identity = SocialAccount.objects.filter(
                user_id=user_id, provider=provider_id
            )
            if not any(
                account_identity(account.extra_data) == (token["oid"], tenant)
                for account in identity
            ):
                raise ValueError
            if (
                not isinstance(token.get("access_token"), str)
                or not token["access_token"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            cls.clear_token(request)
            raise TokenExpiredError("Invalid Graph session. Sign in again.") from exc
        if expiry > timezone.now() + timedelta(seconds=60):
            return token["access_token"]
        if not token.get("refresh_token") and not token.get("msal_cache"):
            raise TokenExpiredError("Graph authorization expired. Sign in again.")
        try:
            token_cache = msal.SerializableTokenCache()
            if token.get("msal_cache") is not None:
                try:
                    token_cache.deserialize(token["msal_cache"])
                    if not isinstance(json.loads(token["msal_cache"]), dict):
                        raise ValueError
                except (ValueError, TypeError, AttributeError) as exc:
                    raise TokenExpiredError("Invalid Graph session. Sign in again.") from exc
            refreshed = exchange_graph_token(
                app,
                refresh_token=token.get("refresh_token"),
                tenant_id=tenant,
                token_cache=token_cache,
            )
            if token.get("oid"):
                profile = GraphClient(refreshed["access_token"], tenant).get_profile()
                if profile.get("id") != token["oid"]:
                    raise TokenExpiredError("Graph identity mismatch. Sign in again.")
        except TokenExpiredError:
            cls.clear_token(request)
            raise
        cls.store_token(
            request,
            provider_id,
            refreshed["access_token"],
            (
                token.get("refresh_token")
                if not list(
                    token_cache.search(msal.TokenCache.CredentialType.REFRESH_TOKEN)
                )
                else None
            ),
            refreshed["expires_in"],
            tenant,
            oid=token.get("oid"),
            token_cache=token_cache,
        )
        return refreshed["access_token"]

    @classmethod
    def clear_token(cls, request, provider_id=None):
        """Remove Graph tokens and related Teams/Graph authentication state.

        The session's Graph token collection is cleared as a whole; ``provider_id``
        is accepted for callers that identify the provider being cleared.
        """
        request.session.pop(cls.SESSION_KEY, None)
        for key in (
            "graph_enabled",
            "crm_teams_authenticated",
            "graph_profile",
            "graph_error",
            "graph_names_from",
        ):
            request.session.pop(key, None)
