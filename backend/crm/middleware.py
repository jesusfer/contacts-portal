from django.conf import settings
from django.core.exceptions import RequestDataTooBig, SuspiciousOperation
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.http.multipartparser import MultiPartParserError
from django.middleware.csrf import CsrfViewMiddleware


class TeamsFrameMiddleware:
    def __init__(self, get_response):
        """Initialize the middleware with the next response handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Allow configured Teams tabs to be framed by Microsoft Teams hosts."""
        response = self.get_response(request)
        is_tab = request.path in (
            "/teams/tabs/personal/",
            "/teams/tabs/shared/config/",
            "/teams/tabs/shared/customer/",
        )
        if settings.TEAMS_ENABLED and is_tab:
            response.headers.pop("X-Frame-Options", None)
            policy = response.get("Content-Security-Policy", "")
            directives = [
                item.strip()
                for item in policy.split(";")
                if item.strip()
                and not item.strip().lower().startswith("frame-ancestors")
            ]
            directives.append(
                "frame-ancestors 'self' https://teams.microsoft.com "
                "https://*.teams.microsoft.com https://*.cloud.microsoft "
                "https://*.office.com https://*.microsoft365.com"
            )
            response["Content-Security-Policy"] = "; ".join(directives)
        return response


class ProfileUploadHandler(MemoryFileUploadHandler):
    def handle_raw_input(
        self, input_data, META, content_length, boundary, encoding=None
    ):
        if content_length and content_length > 6 * 1024 * 1024:
            raise RequestDataTooBig("Picture upload is too large.")
        self.activated = True
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > 5 * 1024 * 1024:
            raise RequestDataTooBig("Picture upload is too large.")
        return super().receive_data_chunk(raw_data, start)


class ProfileCsrfViewMiddleware(CsrfViewMiddleware):
    def process_view(self, request, callback, callback_args, callback_kwargs):
        is_profile = request.path in ("/api/profile/", "/api/profile/picture/")
        if is_profile and request.method == "POST":
            request.upload_handlers = [ProfileUploadHandler(request)]
        try:
            return super().process_view(
                request, callback, callback_args, callback_kwargs
            )
        except (SuspiciousOperation, MultiPartParserError):
            if not is_profile:
                raise
            from .views import error

            return error("Malformed or oversized upload.", 400)
