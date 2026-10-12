from django.contrib import admin
from django.urls import include, path, re_path

from crm import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/session/", views.session),
    path("api/profile/", views.profile),
    path("api/profile/picture/", views.profile_picture),
    path("api/login/", views.password_login),
    path("api/logout/", views.session_logout),
    path("api/companies/", views.companies),
    path("api/contacts/", views.contacts),
    path("api/calls/", views.calls),
    path("api/calls/<int:pk>/", views.call_detail),
    path("api/appointments/", views.appointments),
    path("api/appointments/<int:pk>/", views.appointment_detail),
    path("api/inbox/", views.inbox),
    path("api/inbox/<int:pk>/", views.inbox_detail),
    path("api/kpis/", views.kpis),
    # Graph API endpoints
    path("api/graph/profile/", views.graph_profile),
    path("api/graph/picture/", views.graph_picture),
    path("api/graph/calendar/", views.graph_calendar),
    path("api/graph/inbox/", views.graph_inbox),
    path("api/graph/presence/", views.graph_presence),
    # Teams configuration and SSO
    path("api/teams/config/", views.teams_config),
    path("api/teams/sso/", views.teams_sso),
    path("api/teams/identity/", views.teams_identity),
    path("api/teams/customers/", views.teams_customers),
    path("api/teams/customers/<int:pk>/", views.teams_customer),
    re_path(r"^(?:api|accounts|admin|static|media)(?:/|$)", views.not_found),
    re_path(r"^.*$", views.spa),
]
handler404 = views.not_found
