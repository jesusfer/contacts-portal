"""Tenant-bound Teams authentication and server-side Graph credentials."""
import json
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import requests
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .graph import GraphClient, GraphClientError, TokenExpiredError


class TeamsConfigError(ValueError):
    pass


class TeamsTokenError(ValueError):
    public_message = "Teams sign-in failed. Sign in via OIDC first, then retry."


def graph_provider(provider_id):
    return next((
        app for app in settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get("APPS", [])
        if app["provider_id"] == provider_id and app["name"].lower().startswith("entraid")
    ), None)


def extract_tenant_from_url(server_url):
    try:
        url = urlsplit(server_url)
        parts = url.path.rstrip("/").split("/")
        if url.scheme == "https" and url.netloc == "login.microsoftonline.com" and (
            len(parts) == 2 or (len(parts) == 3 and parts[2] == "v2.0")
        ):
            return str(UUID(parts[1]))
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def provider_tenant(app):
    tenant = extract_tenant_from_url(app["settings"].get("server_url", ""))
    if not tenant or app["settings"].get("graph_tenant_id", tenant) != tenant:
        raise TeamsConfigError("Invalid Graph tenant configuration.")
    return tenant


def load_teams_config(provider_settings):
    config = provider_settings.get("teams")
    tenant = extract_tenant_from_url(provider_settings.get("server_url", ""))
    if not isinstance(config, dict) or not all(
        config.get(key) for key in ("tenant_id", "audience", "resource", "authorized_client_ids")
    ) or config["tenant_id"] != tenant:
        raise TeamsConfigError("Invalid Teams configuration.")
    return config


def _get_jwks(tenant_id, *, reload=False):
    key = f"teams_jwks_{tenant_id}"
    keys = None if reload else cache.get(key)
    if keys is None:
        try:
            response = requests.get(
                f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
                timeout=10, allow_redirects=False,
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
    required = ["exp", "nbf", "iat", "iss", "aud", "tid", "oid", "sub", "scp", "azp"]
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            raise TeamsTokenError("Invalid Teams signing algorithm or key.")
        key = None
        for reload in (False, True):
            for candidate in _get_jwks(config["tenant_id"], reload=reload)["keys"]:
                if candidate.get("kid") == header["kid"]:
                    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(candidate))
                    break
            if key is not None:
                break
        if key is None:
            raise TeamsTokenError("Unknown Teams signing key.")
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=config["audience"],
            issuer=f"https://login.microsoftonline.com/{config['tenant_id']}/v2.0",
            options={"require": required},
        )
        if any(type(claims[key]) not in (int, float) for key in ("exp", "nbf", "iat")):
            raise TeamsTokenError("Invalid Teams token timestamps.")
        if (
            claims["tid"] != config["tenant_id"] or claims["aud"] != config["audience"]
            or any(not isinstance(claims[key], str) or not claims[key] for key in ("oid", "sub", "azp"))
            or claims["azp"] not in config["authorized_client_ids"]
            or not isinstance(claims["scp"], str)
            or "access_as_user" not in claims["scp"].split()
        ):
            raise TeamsTokenError("Invalid Teams identity, client, or scope.")
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise TeamsTokenError("Invalid Teams token.") from exc


def account_identity(extra_data):
    claims = extra_data.get("id_token") if isinstance(extra_data, dict) else None
    if not isinstance(claims, dict):
        claims = extra_data if isinstance(extra_data, dict) else {}
    return claims.get("oid"), claims.get("tid")


def get_teams_account(provider_id, claims):
    matches = [
        account for account in SocialAccount.objects.filter(provider=provider_id).select_related("user")
        if account_identity(account.extra_data) == (claims["oid"], claims["tid"])
    ]
    if len(matches) != 1 or not matches[0].user.is_active:
        raise TeamsTokenError("Teams identity is not linked to an active account. Sign in via OIDC first.")
    return matches[0]


def exchange_graph_token(app, *, assertion=None, refresh_token=None):
    tenant = provider_tenant(app)
    data = {
        "client_id": app["client_id"], "client_secret": app["secret"],
        "scope": "https://graph.microsoft.com/.default offline_access",
    }
    if assertion is not None:
        data.update({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "requested_token_use": "on_behalf_of", "assertion": assertion,
        })
    else:
        data.update({"grant_type": "refresh_token", "refresh_token": refresh_token})
    try:
        response = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data=data, timeout=10, allow_redirects=False,
        )
        if response.status_code in (400, 401, 403):
            raise TokenExpiredError("Graph authorization required. Sign in again.")
        if response.status_code != 200:
            raise GraphClientError("Graph token service unavailable.")
        token = response.json()
        if (
            not isinstance(token, dict) or not isinstance(token.get("access_token"), str)
            or not token["access_token"] or not isinstance(token.get("token_type"), str)
            or token["token_type"].lower() != "bearer"
            or type(token.get("expires_in")) is not int or not 0 < token["expires_in"] <= 86400
            or ("refresh_token" in token and not isinstance(token["refresh_token"], str))
        ):
            raise GraphClientError("Invalid Graph token response.")
    except (requests.RequestException, ValueError) as exc:
        raise GraphClientError("Graph token service unavailable.") from exc
    return token


class GraphTokenManager:
    SESSION_KEY = "graph_tokens"

    @classmethod
    def store_token(cls, request, provider_id, access_token, refresh_token=None, expires_in=3600,
                    tenant_id=None, *, expires_at=None, oid=None):
        expiry = expires_at or timezone.now() + timedelta(seconds=expires_in)
        request.session[cls.SESSION_KEY] = {provider_id: {
            "access_token": access_token, "refresh_token": refresh_token or "",
            "expires_at": expiry.isoformat(), "tenant_id": tenant_id, "oid": oid,
        }}
        request.session["graph_enabled"] = True

    @classmethod
    def get_token(cls, request, provider_id):
        app = graph_provider(provider_id)
        token = request.session.get(cls.SESSION_KEY, {}).get(provider_id)
        if not app or not isinstance(token, dict):
            raise TokenExpiredError("Graph authorization required. Sign in again.")
        try:
            tenant = provider_tenant(app)
            expiry = datetime.fromisoformat(token["expires_at"])
            if timezone.is_naive(expiry) or token.get("tenant_id") != tenant:
                raise ValueError
            if not isinstance(token.get("access_token"), str) or not token["access_token"]:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            cls.clear_token(request)
            raise TokenExpiredError("Invalid Graph session. Sign in again.") from exc
        if expiry > timezone.now() + timedelta(seconds=60):
            return token["access_token"]
        if not token.get("refresh_token"):
            raise TokenExpiredError("Graph authorization expired. Sign in again.")
        try:
            refreshed = exchange_graph_token(app, refresh_token=token["refresh_token"])
            if token.get("oid"):
                profile = GraphClient(refreshed["access_token"], tenant).get_profile()
                if profile.get("id") != token["oid"]:
                    raise TokenExpiredError("Graph identity mismatch. Sign in again.")
        except TokenExpiredError:
            cls.clear_token(request)
            raise
        cls.store_token(
            request, provider_id, refreshed["access_token"],
            refreshed.get("refresh_token") or token["refresh_token"], refreshed["expires_in"],
            tenant, oid=token.get("oid"),
        )
        return refreshed["access_token"]

    @classmethod
    def clear_token(cls, request, provider_id=None):
        request.session.pop(cls.SESSION_KEY, None)
        for key in ("graph_enabled", "crm_teams_authenticated", "graph_profile", "graph_error", "graph_names_from"):
            request.session.pop(key, None)
