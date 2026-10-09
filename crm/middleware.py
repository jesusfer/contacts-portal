from django.core.exceptions import RequestDataTooBig, SuspiciousOperation
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.http.multipartparser import MultiPartParserError
from django.middleware.csrf import CsrfViewMiddleware


class ProfileUploadHandler(MemoryFileUploadHandler):
    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
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
            return super().process_view(request, callback, callback_args, callback_kwargs)
        except (SuspiciousOperation, MultiPartParserError):
            if not is_profile:
                raise
            from .views import error

            return error("Malformed or oversized upload.", 400)
