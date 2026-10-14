from functools import partial

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .external_calls import oidc_request


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def get_requests_session(self):
        session = super().get_requests_session()
        session.request = partial(oidc_request, session.request)
        return session

    def is_open_for_signup(self, request, sociallogin):
        return True

    def can_authenticate_by_email(self, login, email):
        # Only an explicitly connected provider subject may identify an existing user.
        return False
