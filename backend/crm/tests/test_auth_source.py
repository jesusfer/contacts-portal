from unittest.mock import patch

from allauth.account.signals import user_logged_in as allauth_user_logged_in
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.internal.flows.login import complete_login
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.core.cache import cache
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path

from config.settings import configure_oidc
from config.urls import urlpatterns as project_urlpatterns
from ..adapters import SocialAccountAdapter
from ..signals import AUTH_SOURCE_SESSION_KEY


PROVIDERS = configure_oidc(
    '[{"id":"corporate","name":"Entra ID","server_url":"https://entra.example.com",'
    '"client_id":"entra-client","client_secret":"fixture"},'
    '{"id":"partner","name":"Ping","server_url":"https://ping.example.com",'
    '"client_id":"ping-client","client_secret":"fixture"}]',
    debug=False,
)


def verified_social_callback(request, provider_id, subject, process):
    # Exercise the completion boundary after the OAuth adapter verifies claims.
    provider = get_adapter().get_provider(request, provider_id)
    sociallogin = provider.sociallogin_from_response(request, {"sub": subject})
    sociallogin.state = {"process": process}
    return complete_login(request, sociallogin)


urlpatterns = [
    path("test/social/<provider_id>/<subject>/<process>/", verified_social_callback),
    *project_urlpatterns,
]


@override_settings(
    ROOT_URLCONF=__name__,
    SOCIALACCOUNT_PROVIDERS=PROVIDERS,
    SECURE_SSL_REDIRECT=False,
    ACCOUNT_AUTHENTICATED_LOGIN_REDIRECTS=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class LoginSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from allauth.socialaccount.models import SocialAccount

        cls.user = get_user_model().objects.create_user("agent", is_staff=True)
        cls.user.set_password("test-passphrase-123")
        cls.user.save()
        for provider_id in ("corporate", "partner"):
            SocialAccount.objects.create(
                user=cls.user, provider=provider_id, uid="linked-subject"
            )

    def setUp(self):
        cache.clear()

    def source(self):
        return self.client.get("/api/session/").json()["user"]["auth_source"]

    def social_login(self, provider_id, *, subject="linked-subject", process="login"):
        return self.client.get(f"/test/social/{provider_id}/{subject}/{process}/")

    def mark_source(self, provider_id="corporate"):
        session = self.client.session
        session[AUTH_SOURCE_SESSION_KEY] = provider_id
        session.save()

    def local_login(self, credential=None):
        if credential is None:
            credential = "test-passphrase-123"
        return self.client.post(
            "/api/login/",
            {"username": self.user.username, "password": credential},
            content_type="application/json",
        )

    def test_anonymous_and_existing_local_sessions(self):
        self.assertIsNone(self.client.get("/api/session/").json()["user"])
        self.client.force_login(self.user)
        self.assertEqual(self.source(), "Local")
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_api_local_login_does_not_use_linked_identities(self):
        response = self.local_login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["auth_source"], "Local")
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_actual_social_completion_selects_provider_for_same_user(self):
        for provider_id, expected in (("partner", "Ping"), ("corporate", "Entra ID"), ("partner", "Ping")):
            with self.subTest(provider_id=provider_id):
                self.assertEqual(self.social_login(provider_id).status_code, 302)
                response = self.client.get("/api/session/").json()
                self.assertEqual(response["user"]["id"], self.user.pk)
                self.assertEqual(response["user"]["auth_source"], expected)
                self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], provider_id)
                self.assertEqual(
                    set(response["user"]), {"id", "username", "name", "auth_source", "picture_url"}
                )
                self.assertNotIn("fixture", str(response))
                self.assertNotIn("linked-subject", str(response))

    def test_social_signal_runs_after_django_local_reset(self):
        observed = []

        def django_observer(sender, request, **kwargs):
            observed.append(("django", request.session.get(AUTH_SOURCE_SESSION_KEY)))

        def allauth_observer(sender, request, sociallogin, **kwargs):
            observed.append(("allauth", request.session.get(AUTH_SOURCE_SESSION_KEY)))
            self.assertEqual(sociallogin.provider.id, "openid_connect")
            self.assertEqual(sociallogin.provider.sub_id, "partner")

        user_logged_in.connect(django_observer)
        allauth_user_logged_in.connect(allauth_observer)
        try:
            self.assertEqual(self.social_login("partner").status_code, 302)
        finally:
            user_logged_in.disconnect(django_observer)
            allauth_user_logged_in.disconnect(allauth_observer)
        self.assertEqual(observed, [("django", None), ("allauth", "partner")])

    def test_social_signup_completion_records_provider(self):
        self.assertEqual(
            self.social_login("corporate", subject="new-user-subject").status_code, 302
        )
        response = self.client.get("/api/session/").json()
        self.assertNotEqual(response["user"]["id"], self.user.pk)
        self.assertEqual(response["user"]["auth_source"], "Entra ID")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "corporate")
        self.assertIsNone(response["agent"])

    def test_api_local_relogin_resets_social_source_for_same_user(self):
        self.social_login("partner")
        response = self.local_login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], self.user.pk)
        self.assertEqual(self.source(), "Local")
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_failed_api_login_preserves_active_source(self):
        self.social_login("partner")
        self.assertEqual(self.local_login("wrong").status_code, 401)
        self.assertEqual(self.source(), "Ping")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "partner")

    def test_failed_anonymous_login_does_not_set_source(self):
        self.assertEqual(self.local_login("wrong").status_code, 401)
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_rate_limited_api_login_preserves_active_source(self):
        self.social_login("partner")
        for _ in range(5):
            self.assertEqual(self.local_login("wrong").status_code, 401)
        self.assertEqual(self.local_login().status_code, 429)
        self.assertEqual(self.source(), "Ping")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "partner")

    def test_rejected_social_login_preserves_active_source(self):
        self.social_login("partner")
        with patch.object(
            SocialAccountAdapter,
            "pre_social_login",
            side_effect=ImmediateHttpResponse(HttpResponse(status=401)),
        ):
            self.assertEqual(self.social_login("corporate").status_code, 401)
        self.assertEqual(self.source(), "Ping")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "partner")

    def test_social_connect_does_not_change_active_source(self):
        self.social_login("partner")
        with patch.object(SocialAccountAdapter, "send_notification_mail"):
            self.assertEqual(
                self.social_login("corporate", subject="new-subject", process="connect").status_code,
                302,
            )
        self.assertTrue(
            self.user.socialaccount_set.filter(provider="corporate", uid="new-subject").exists()
        )
        self.assertEqual(self.source(), "Ping")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "partner")

    def test_social_connect_does_not_change_local_source(self):
        self.local_login()
        with patch.object(SocialAccountAdapter, "send_notification_mail"):
            self.assertEqual(
                self.social_login("corporate", subject="new-subject", process="connect").status_code,
                302,
            )
        self.assertEqual(self.source(), "Local")
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_unknown_removed_or_unconfigured_provider_is_oidc(self):
        self.social_login("corporate")
        with override_settings(SOCIALACCOUNT_PROVIDERS={}):
            self.assertEqual(self.source(), "OIDC")
        with override_settings(
            SOCIALACCOUNT_PROVIDERS={"openid_connect": {"APPS": [PROVIDERS["openid_connect"]["APPS"][1]]}}
        ):
            self.assertEqual(self.source(), "OIDC")
        self.mark_source("unknown")
        self.assertEqual(self.source(), "OIDC")

    def test_display_name_is_exact_current_config_not_stored_label(self):
        self.social_login("corporate")
        renamed = {
            "openid_connect": {
                "APPS": [
                    {**app, "name": "Our custom identity service"}
                    for app in PROVIDERS["openid_connect"]["APPS"]
                ]
            }
        }
        with override_settings(SOCIALACCOUNT_PROVIDERS=renamed):
            self.assertEqual(self.source(), "Our custom identity service")
        self.assertEqual(self.client.session[AUTH_SOURCE_SESSION_KEY], "corporate")

    def test_allauth_password_login_resets_source_even_with_allauth_backend(self):
        self.social_login("partner")
        response = self.client.post(
            "/accounts/login/",
            {"login": self.user.username, "password": "test-passphrase-123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.source(), "Local")
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_failed_allauth_password_login_preserves_source(self):
        self.social_login("partner")
        response = self.client.post(
            "/accounts/login/", {"login": self.user.username, "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.source(), "Ping")

    def test_admin_password_login_resets_source(self):
        self.social_login("partner")
        response = self.client.post(
            "/admin/login/",
            {"username": self.user.username, "password": "test-passphrase-123", "next": "/admin/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.source(), "Local")
        self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)

    def test_failed_admin_password_login_preserves_source(self):
        self.social_login("partner")
        response = self.client.post(
            "/admin/login/", {"username": self.user.username, "password": "wrong", "next": "/admin/"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.source(), "Ping")

    def test_api_and_allauth_logout_clear_source(self):
        for logout_url in ("/api/logout/", "/accounts/logout/"):
            with self.subTest(logout_url=logout_url):
                self.social_login("partner")
                self.assertIn(AUTH_SOURCE_SESSION_KEY, self.client.session)
                self.assertIn(self.client.post(logout_url).status_code, (200, 302))
                self.assertNotIn(AUTH_SOURCE_SESSION_KEY, self.client.session)
                self.assertIsNone(self.client.get("/api/session/").json()["user"])
