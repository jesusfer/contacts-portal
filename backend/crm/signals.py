from allauth.account.signals import user_logged_in as allauth_user_logged_in
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .teams import GraphTokenManager, account_identity, graph_provider, provider_tenant


AUTH_SOURCE_SESSION_KEY = "crm_auth_source"


@receiver(user_logged_in)
@receiver(allauth_user_logged_in)
def track_login_source(sender, request, user, sociallogin=None, **kwargs):
    GraphTokenManager.clear_token(request)
    if sociallogin is None:
        request.session.pop(AUTH_SOURCE_SESSION_KEY, None)
    else:
        request.session[AUTH_SOURCE_SESSION_KEY] = sociallogin.provider.sub_id


@receiver(allauth_user_logged_in)
def store_graph_tokens(sender, request, user, sociallogin=None, **kwargs):
    if sociallogin is None:
        return
    provider_id = sociallogin.provider.sub_id
    app = graph_provider(provider_id)
    token = sociallogin.token
    if not app or token is None or not token.token or token.expires_at is None:
        return
    oid, tid = account_identity(sociallogin.account.extra_data)
    try:
        tenant = provider_tenant(app, tid)
    except ValueError:
        return
    GraphTokenManager.store_token(
        request, provider_id, token.token, token.token_secret,
        tenant_id=tenant, expires_at=token.expires_at, oid=oid,
    )


@receiver(user_logged_out)
def clear_login_source_and_tokens(sender, request, **kwargs):
    request.session.pop(AUTH_SOURCE_SESSION_KEY, None)
    GraphTokenManager.clear_token(request)
