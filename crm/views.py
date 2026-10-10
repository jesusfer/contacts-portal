import json
from functools import wraps

from allauth.core import ratelimit
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.exceptions import ObjectDoesNotExist, RequestDataTooBig, SuspiciousOperation, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .forms import AppointmentForm, CallForm, CompanyForm, ContactForm, EmailForm
from .models import Agent, Appointment, Call, Company, Contact, Email, UserProfile
from .pictures import normalize_picture
from .signals import AUTH_SOURCE_SESSION_KEY


def error(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def csrf_failure(request, reason=""):
    return error("CSRF verification failed. Refresh your session and try again.", 403)


def api(methods, *, agent_required=True):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = error("Method not allowed.", 405)
                response["Allow"] = ", ".join(methods)
                return response
            if agent_required:
                if not request.user.is_authenticated:
                    return error("Authentication required.", 401)
                try:
                    request.agent = Agent.objects.get(user=request.user)
                except Agent.DoesNotExist:
                    return error("Your account has not been provisioned with an agent.", 403)
            try:
                return view(request, *args, **kwargs)
            except Http404:
                return error("Not found.", 404)
            except ValidationError as exc:
                return error("Validation failed.", fields=getattr(exc, "message_dict", {"__all__": exc.messages}))
            except IntegrityError:
                return error("The data conflicts with an existing record or constraint.", 400)
            except (SuspiciousOperation, MultiPartParserError):
                return error("Malformed or oversized request.", 400)
        return wrapped
    return decorate


def payload(request, schema, *, required=()):
    if request.content_type != "application/json":
        raise ValidationError("Content-Type must be application/json.")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RequestDataTooBig) as exc:
        raise ValidationError("Malformed JSON body.") from exc
    if not isinstance(data, dict):
        raise ValidationError("The JSON body must be an object.")
    unexpected = data.keys() - schema.keys()
    if unexpected:
        raise ValidationError("Unknown fields: " + ", ".join(sorted(unexpected)))
    missing = set(required) - data.keys()
    if missing:
        raise ValidationError("Required fields: " + ", ".join(sorted(missing)))
    for key, value in data.items():
        if type(value) not in schema[key]:
            raise ValidationError({key: "Invalid value type."})
        if key.endswith("_id") and value <= 0:
            raise ValidationError({key: "Use a positive integer ID."})
    return data


TEXT = (str,)
ID = (int,)
BOOL = (bool,)
RATING = (int, type(None))


def save_form(form_class, instance, changes):
    data = {}
    for field in form_class.Meta.fields:
        value = getattr(instance, field)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        data[field] = value
    data.update(changes)
    form = form_class(data=data, instance=instance)
    if not form.is_valid():
        raise ValidationError({field: list(messages) for field, messages in form.errors.items()})
    return form.save()


def company_json(company):
    return {"id": company.pk, "name": company.name}


def contact_json(contact, *, company=True):
    result = {"id": contact.pk, "name": contact.name, "email": contact.email}
    if company:
        result["company"] = company_json(contact.company)
    return result


def appointment_fields(appointment):
    return {
        "id": appointment.pk,
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
        "notes": appointment.notes,
    }


def call_json(call):
    try:
        appointment = appointment_fields(call.appointment)
    except ObjectDoesNotExist:
        appointment = None
    return {
        "id": call.pk,
        "contact": contact_json(call.contact),
        "status": call.status,
        "was_rescheduled": call.was_rescheduled,
        "summary": call.summary,
        "rating": call.rating,
        "appointment": appointment,
    }


def appointment_json(appointment):
    return {
        **appointment_fields(appointment),
        "call": {
            "id": appointment.call_id,
            "status": appointment.call.status,
            "was_rescheduled": appointment.call.was_rescheduled,
            "contact": contact_json(appointment.call.contact),
        },
    }


def email_json(email):
    return {
        "id": email.pk,
        "contact": contact_json(email.contact, company=False),
        "subject": email.subject,
        "body": email.body,
        "received_at": email.received_at.isoformat(),
        "is_read": email.is_read,
        "is_replied": email.is_replied,
    }


def session_json(request):
    user = None
    agent = None
    if request.user.is_authenticated:
        provider_id = request.session.get(AUTH_SOURCE_SESSION_KEY)
        auth_source = "Local"
        if provider_id:
            auth_source = next(
                (
                    app["name"]
                    for app in settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get("APPS", [])
                    if app["provider_id"] == provider_id
                ),
                "OIDC",
            )
        user = {
            "id": request.user.pk,
            "username": request.user.get_username(),
            "name": request.user.get_full_name() or request.user.get_username(),
            "auth_source": auth_source,
            "picture_url": picture_url(request.user),
        }
        try:
            profile = Agent.objects.prefetch_related("companies").get(user=request.user)
            companies = [company_json(company) for company in profile.companies.all()]
            agent = {
                "id": profile.pk,
                "companies": companies,
            }
        except Agent.DoesNotExist:
            pass
    providers = [
        {
            "id": app["provider_id"], "name": app["name"],
            "login_url": reverse("openid_connect_login", kwargs={"provider_id": app["provider_id"]}),
        }
        for app in settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get("APPS", [])
    ]
    return {"user": user, "agent": agent, "providers": providers}


def picture_url(user):
    return "/api/profile/picture/" if UserProfile.objects.filter(user=user).exclude(picture="").exists() else None


def profile_json(user):
    return {
        "id": user.pk,
        "username": user.get_username(),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "picture_url": picture_url(user),
    }


@api(["GET", "PATCH"], agent_required=False)
def profile(request):
    if not request.user.is_authenticated:
        return error("Authentication required.", 401)
    if request.method == "PATCH":
        data = payload(request, {"first_name": TEXT, "last_name": TEXT})
        for field, value in data.items():
            if len(value) > 150:
                raise ValidationError({field: "Use at most 150 characters."})
        for field, value in data.items():
            setattr(request.user, field, value)
        if data:
            request.user.save(update_fields=list(data))
    return JsonResponse(profile_json(request.user))


@api(["GET", "POST", "DELETE"], agent_required=False)
def profile_picture(request):
    if not request.user.is_authenticated:
        return error("Authentication required.", 401)
    if request.method == "GET":
        item = get_object_or_404(UserProfile.objects.exclude(picture=""), user=request.user)
        try:
            stream = item.picture.open("rb")
        except FileNotFoundError:
            raise Http404
        response = FileResponse(stream, content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = 'inline; filename="profile.jpg"'
        return response
    normalized = None
    if request.method == "POST":
        if request.content_type != "multipart/form-data":
            raise ValidationError("Content-Type must be multipart/form-data.")
        if request.POST or set(request.FILES) != {"picture"} or len(request.FILES.getlist("picture")) != 1:
            raise ValidationError("Upload exactly one picture field and no other fields.")
        normalized = normalize_picture(request.FILES["picture"])
    storage = UserProfile._meta.get_field("picture").storage
    new_name = None
    try:
        with transaction.atomic():
            # Lock the owner even before a profile exists to serialize first uploads.
            user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            item = UserProfile.objects.filter(user=user).first()
            if item is None and normalized is not None:
                item = UserProfile.objects.create(user=user)
            if item is not None:
                old_name = item.picture.name
                if normalized is not None:
                    item.picture.save("picture.jpg", normalized, save=False)
                    new_name = item.picture.name
                else:
                    item.picture = ""
                item.save(update_fields=["picture"])
                if old_name:
                    transaction.on_commit(lambda: storage.delete(old_name), robust=True)
    except Exception:
        if new_name:
            storage.delete(new_name)
        raise
    return JsonResponse(profile_json(request.user))


@ensure_csrf_cookie
@api(["GET"], agent_required=False)
def session(request):
    return JsonResponse(session_json(request))


@api(["POST"], agent_required=False)
def password_login(request):
    data = payload(request, {"username": TEXT, "password": TEXT}, required=("username", "password"))
    if not ratelimit.consume(request, action="login") or not ratelimit.consume(
        request, action="login_failed", key=data["username"].casefold(), dry_run=True
    ):
        response = error("Too many login attempts. Try again later.", 429)
        response["Retry-After"] = "300"
        return response
    user = authenticate(request, **data)
    if user is None:
        ratelimit.consume(request, action="login_failed", key=data["username"].casefold())
        return error("Invalid username or password.", 401)
    login(request, user)
    ratelimit.clear(request, action="login_failed", key=data["username"].casefold())
    return JsonResponse(session_json(request))


@api(["POST"], agent_required=False)
def session_logout(request):
    if not request.user.is_authenticated:
        return error("Authentication required.", 401)
    logout(request)
    return JsonResponse(session_json(request))


def directory_contacts():
    return Contact.objects.select_related("company")


def scoped_calls(request):
    return Call.objects.filter(agent=request.agent).select_related("contact__company", "appointment")


def scoped_appointments(request):
    return Appointment.objects.filter(call__agent=request.agent).select_related("call__contact__company")


def scoped_emails(request):
    return Email.objects.filter(agent=request.agent).select_related("contact")


@api(["GET", "POST"])
def companies(request):
    if request.method == "GET":
        return JsonResponse({"companies": [company_json(item) for item in Company.objects.all()]})
    data = payload(request, {"name": TEXT}, required=("name",))
    company = save_form(CompanyForm, Company(), data)
    return JsonResponse(company_json(company), status=201)


@api(["GET", "POST"])
def contacts(request):
    if request.method == "GET":
        return JsonResponse({"contacts": [contact_json(item) for item in directory_contacts()]})
    data = payload(
        request, {"name": TEXT, "email": TEXT, "company_id": ID},
        required=("name", "email", "company_id"),
    )
    company = get_object_or_404(Company, pk=data.pop("company_id"))
    contact = save_form(ContactForm, Contact(company=company), data)
    return JsonResponse(contact_json(contact), status=201)


@api(["GET", "POST"])
def calls(request):
    if request.method == "GET":
        return JsonResponse({"calls": [call_json(item) for item in scoped_calls(request)]})
    data = payload(
        request, {"contact_id": ID, "status": TEXT, "summary": TEXT, "rating": RATING},
        required=("contact_id",),
    )
    contact = get_object_or_404(directory_contacts(), pk=data.pop("contact_id"))
    call = save_form(CallForm, Call(agent=request.agent, contact=contact), data)
    return JsonResponse(call_json(call), status=201)


@api(["GET", "PATCH"])
def call_detail(request, pk):
    if request.method == "GET":
        return JsonResponse(call_json(get_object_or_404(scoped_calls(request), pk=pk)))
    data = payload(request, {"status": TEXT, "summary": TEXT, "rating": RATING})
    with transaction.atomic():
        call = get_object_or_404(scoped_calls(request).select_for_update(), pk=pk)
        call = save_form(CallForm, call, data)
    return JsonResponse(call_json(call))


@api(["GET", "POST"])
def appointments(request):
    if request.method == "GET":
        return JsonResponse({"appointments": [appointment_json(item) for item in scoped_appointments(request)]})
    data = payload(
        request, {"call_id": ID, "starts_at": TEXT, "ends_at": TEXT, "notes": TEXT},
        required=("call_id", "starts_at", "ends_at"),
    )
    call = get_object_or_404(scoped_calls(request), pk=data.pop("call_id"))
    appointment = save_form(AppointmentForm, Appointment(call=call), data)
    return JsonResponse(appointment_json(appointment), status=201)


@api(["GET", "PATCH"])
def appointment_detail(request, pk):
    if request.method == "GET":
        return JsonResponse(appointment_json(get_object_or_404(scoped_appointments(request), pk=pk)))
    data = payload(request, {"starts_at": TEXT, "ends_at": TEXT, "notes": TEXT})
    with transaction.atomic():
        appointment = get_object_or_404(scoped_appointments(request).select_for_update(), pk=pk)
        appointment = save_form(AppointmentForm, appointment, data)
        appointment.refresh_from_db()
    return JsonResponse(appointment_json(appointment))


@api(["GET", "POST"])
def inbox(request):
    if request.method == "GET":
        return JsonResponse({"emails": [email_json(item) for item in scoped_emails(request)]})
    data = payload(
        request,
        {
            "contact_id": ID, "subject": TEXT, "body": TEXT, "received_at": TEXT,
            "is_read": BOOL, "is_replied": BOOL,
        },
        required=("contact_id", "subject"),
    )
    contact = get_object_or_404(directory_contacts(), pk=data.pop("contact_id"))
    email = save_form(EmailForm, Email(agent=request.agent, contact=contact, received_at=timezone.now()), data)
    return JsonResponse(email_json(email), status=201)


@api(["PATCH"])
def inbox_detail(request, pk):
    data = payload(request, {"is_read": BOOL, "is_replied": BOOL})
    with transaction.atomic():
        email = get_object_or_404(scoped_emails(request).select_for_update(), pk=pk)
        # These flags track activity only; this endpoint never sends email.
        email = save_form(EmailForm, email, data)
    return JsonResponse(email_json(email))


def aggregates():
    return {
        "total_calls": Count("id"),
        "completed_calls": Count("id", filter=Q(status=Call.Status.COMPLETED)),
        "average_rating": Avg("rating", filter=Q(status=Call.Status.COMPLETED)),
    }


@api(["GET"])
def kpis(request):
    queryset = scoped_calls(request).order_by()
    totals = queryset.aggregate(
        **aggregates(),
        non_scheduled=Count("id", filter=~Q(status=Call.Status.SCHEDULED)),
        missed=Count("id", filter=Q(status=Call.Status.MISSED)),
        rescheduled=Count(
            "id",
            filter=(Q(was_rescheduled=True) | Q(status=Call.Status.RESCHEDULED))
            & ~Q(status=Call.Status.SCHEDULED),
        ),
    )
    denominator = totals.pop("non_scheduled")
    totals["missed_percentage"] = 100 * totals.pop("missed") / denominator if denominator else 0
    totals["rescheduled_percentage"] = 100 * totals.pop("rescheduled") / denominator if denominator else 0
    for key, prefix in (("by_contact", "contact"), ("by_company", "contact__company")):
        rows = queryset.values(f"{prefix}__id", f"{prefix}__name").annotate(**aggregates()).order_by(
            f"{prefix}__name", f"{prefix}__id"
        )
        totals[key] = [
            {
                "id": row.pop(f"{prefix}__id"),
                "name": row.pop(f"{prefix}__name"),
                **row,
            }
            for row in rows
        ]
    return JsonResponse(totals)


def spa(request):
    if request.method not in ("GET", "HEAD"):
        return error("Method not allowed.", 405)
    index = settings.FRONTEND_DIST / "index.html"
    if not index.is_file():
        return error("Frontend build unavailable. Build frontend/dist first.", 503)
    response = FileResponse(index.open("rb"), content_type="text/html")
    response["Cache-Control"] = "no-cache"
    return response


def not_found(request, exception=None):
    return error("Not found.", 404)
