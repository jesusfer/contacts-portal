from allauth.account.signals import user_logged_in as allauth_user_logged_in
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver


AUTH_SOURCE_SESSION_KEY = "crm_auth_source"


@receiver(user_logged_in)
@receiver(allauth_user_logged_in)
def track_login_source(sender, request, user, sociallogin=None, **kwargs):
    if sociallogin is None:
        request.session.pop(AUTH_SOURCE_SESSION_KEY, None)
    else:
        # Allauth emits this after Django's login signal, only on completed login.
        # Its provider.sub_id identifies the configured OIDC app, not the type.
        request.session[AUTH_SOURCE_SESSION_KEY] = sociallogin.provider.sub_id


@receiver(user_logged_out)
def clear_login_source(sender, request, **kwargs):
    request.session.pop(AUTH_SOURCE_SESSION_KEY, None)
