import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.http.multipartparser import MultiPartParserError
from django.test import Client, TestCase, override_settings
from PIL import Image

from ..models import Agent, UserProfile
from ..signals import AUTH_SOURCE_SESSION_KEY


@override_settings(SECURE_SSL_REDIRECT=False)
class ProfileTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory(dir=settings.BASE_DIR, prefix=".test-profile-")
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user(
            "owner", email="owner@example.com", first_name="Old", last_name="Name",
        )
        self.other = get_user_model().objects.create_user("other", email="other@example.com")
        self.client.force_login(self.user)

    def image(self, fmt="PNG", size=(800, 600), filename="input.png", mime=None, metadata=False):
        output = BytesIO()
        image = Image.new("RGB", size, "red")
        kwargs = {}
        if metadata:
            exif = Image.Exif()
            exif[270] = "sensitive metadata"
            exif[274] = 6
            kwargs["exif"] = exif
        image.save(output, format=fmt, **kwargs)
        return SimpleUploadedFile(
            filename, output.getvalue(),
            content_type=mime or {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[fmt],
        )

    def upload(self, picture=None, client=None):
        with self.captureOnCommitCallbacks(execute=True):
            return (client or self.client).post(
                "/api/profile/picture/", {"picture": picture or self.image()},
            )

    def test_get_no_agent_or_profile_creation(self):
        self.assertFalse(Agent.objects.filter(user=self.user).exists())
        self.assertEqual(self.client.get("/api/profile/").json(), {
            "id": self.user.pk, "username": "owner", "first_name": "Old",
            "last_name": "Name", "email": "owner@example.com", "picture_url": None,
        })
        self.assertIsNone(self.client.get("/api/session/").json()["user"]["picture_url"])
        self.assertFalse(UserProfile.objects.exists())
        session = self.client.session
        session[AUTH_SOURCE_SESSION_KEY] = "oidc-provider"
        session.save()
        self.assertEqual(self.client.get("/api/profile/").status_code, 200)
        self.assertEqual(self.client.patch("/api/profile/", {"first_name": "OIDC"}, content_type="application/json").status_code, 200)

    def test_names_persist_and_session_reflects_change(self):
        response = self.client.patch(
            "/api/profile/", {"first_name": "New", "last_name": "Person"}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "New Person")
        self.assertEqual(self.client.get("/api/session/").json()["user"]["name"], "New Person")
        self.assertEqual(self.client.patch(
            "/api/profile/", {"first_name": "", "last_name": ""}, content_type="application/json",
        ).status_code, 200)
        self.assertEqual(self.client.get("/api/session/").json()["user"]["name"], "owner")
        self.other.refresh_from_db()
        self.assertEqual(self.other.first_name, "")
        self.assertEqual(self.other.email, "other@example.com")

    def test_json_validation_and_immutable_identity(self):
        invalid = [
            {"username": "other"}, {"email": "attacker@example.com"}, {"roles": ["admin"]},
            {"company_id": 1}, {"user_id": self.other.pk}, {"id": self.other.pk},
            {"is_staff": True}, {"last_name": None}, {"first_name": 1},
            {"first_name": "x" * 151}, {"last_name": "x" * 151}, [],
        ]
        for data in invalid:
            with self.subTest(data=data):
                response = self.client.patch("/api/profile/", data=json.dumps(data), content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        self.assertEqual(self.client.patch("/api/profile/", {"last_name": "x" * 150}, content_type="application/json").status_code, 200)
        self.assertEqual(self.client.patch("/api/profile/", {}, content_type="application/json").status_code, 200)
        for body in ("{", b"\xff"):
            self.assertEqual(self.client.patch("/api/profile/", body, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.patch("/api/profile/", "first_name=a", content_type="text/plain").status_code, 400)
        with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=10):
            self.assertEqual(self.client.patch("/api/profile/", '{"first_name":"too long"}', content_type="application/json").status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "owner")
        self.assertEqual(self.user.email, "owner@example.com")
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.user.first_name, "Old")

    def test_all_formats_normalize_private_jpeg_without_metadata(self):
        for fmt in ("JPEG", "PNG", "WEBP"):
            with self.subTest(fmt=fmt):
                response = self.upload(self.image(fmt, filename="../../suspicious.svg", metadata=True))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["picture_url"], "/api/profile/picture/")
                self.assertEqual(self.client.get("/api/session/").json()["user"]["picture_url"], "/api/profile/picture/")
                item = UserProfile.objects.get(user=self.user)
                self.assertRegex(item.picture.name, r"^profile-pictures/[a-f0-9]{32}\.jpg$")
                self.assertNotIn(item.picture.name, response.content.decode())
                result = self.client.get("/api/profile/picture/")
                self.assertEqual(result["Content-Type"], "image/jpeg")
                self.assertEqual(result["Cache-Control"], "private, no-store")
                self.assertEqual(result["X-Content-Type-Options"], "nosniff")
                data = b"".join(result.streaming_content)
                result.close()
                with Image.open(BytesIO(data)) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertLessEqual(max(image.size), 512)
                    self.assertEqual(image.getexif(), {})
                    self.assertNotIn("icc_profile", image.info)
                    self.assertEqual(image.size, (384, 512))
                self.assertEqual(len(list(Path(self.media.name).rglob("*.jpg"))), 1)

    def test_invalid_uploads_retain_previous_picture(self):
        self.upload()
        old = UserProfile.objects.get(user=self.user).picture.name
        invalid = [
            SimpleUploadedFile("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', content_type="image/svg+xml"),
            SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg"),
            SimpleUploadedFile("empty.jpg", b"", content_type="image/jpeg"),
            SimpleUploadedFile("large.jpg", b"x" * (5 * 1024 * 1024 + 1), content_type="image/jpeg"),
            self.image("GIF"), self.image("PNG", mime="image/jpeg"),
            self.image(size=(4097, 1)),
        ]
        for picture in invalid:
            with self.subTest(name=picture.name, size=picture.size):
                self.assertEqual(self.upload(picture).status_code, 400)
                self.assertEqual(UserProfile.objects.get(user=self.user).picture.name, old)
                self.assertTrue(Path(self.media.name, old).exists())
        broken = self.image()
        broken.file = BytesIO(broken.read()[:30])
        broken.size = 30
        self.assertEqual(self.upload(broken).status_code, 400)
        with patch("crm.pictures.Image.open", side_effect=Image.DecompressionBombError):
            self.assertEqual(self.upload().status_code, 400)
        with patch("crm.pictures.Image.open", side_effect=Image.DecompressionBombWarning):
            self.assertEqual(self.upload().status_code, 400)

    def test_multipart_errors_and_unknown_fields(self):
        for fields in ({}, {"picture": self.image(), "user_id": self.other.pk},
                       {"other": self.image()}, {"picture": [self.image(), self.image()]},
                       {"picture": self.image(), "other": self.image()}):
            response = self.client.post("/api/profile/picture/", fields)
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json())
        for content_type in ("application/json", "multipart/form-data", "multipart/form-data; boundary=\x01"):
            response = self.client.post("/api/profile/picture/", b"{}", content_type=content_type)
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json())
        self.assertEqual(self.upload(SimpleUploadedFile(
            "too-large.png", b"x" * (6 * 1024 * 1024 + 1), content_type="image/png",
        )).status_code, 400)
        with patch("django.http.request.MultiPartParser", side_effect=MultiPartParserError("invalid")):
            response = self.upload()
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.json())

    def test_cleanup_replacement_delete_and_failed_database_save(self):
        self.upload()
        old = UserProfile.objects.get(user=self.user).picture.name
        with patch.object(UserProfile, "save", side_effect=IntegrityError):
            self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(UserProfile.objects.get(user=self.user).picture.name, old)
        self.assertEqual(len(list(Path(self.media.name).rglob("*.jpg"))), 1)
        self.upload()
        new = UserProfile.objects.get(user=self.user).picture.name
        self.assertNotEqual(old, new)
        self.assertFalse(Path(self.media.name, old).exists())
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete("/api/profile/picture/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["picture_url"])
        self.assertFalse(Path(self.media.name, new).exists())
        self.assertEqual(self.client.get("/api/profile/picture/").status_code, 404)
        self.assertEqual(self.client.delete("/api/profile/picture/").status_code, 200)

    def test_storage_failure_keeps_old_picture_and_cleanup_failure_keeps_new(self):
        self.upload()
        old = UserProfile.objects.get(user=self.user).picture.name
        storage = UserProfile._meta.get_field("picture").storage
        with patch.object(storage, "save", side_effect=OSError("Storage unavailable")):
            with self.assertRaises(OSError):
                self.upload()
        self.assertEqual(UserProfile.objects.get(user=self.user).picture.name, old)
        self.assertTrue(Path(self.media.name, old).exists())
        with patch.object(storage, "delete", side_effect=OSError("Cleanup unavailable")):
            with self.assertLogs(level="ERROR"):
                self.assertEqual(self.upload().status_code, 200)
        new = UserProfile.objects.get(user=self.user).picture.name
        self.assertNotEqual(old, new)
        self.assertTrue(Path(self.media.name, new).exists())

    def test_ownership_no_public_media_and_authentication(self):
        self.upload()
        item = UserProfile.objects.get(user=self.user)
        second = Client()
        second.force_login(self.other)
        self.assertEqual(second.get("/api/profile/").json()["id"], self.other.pk)
        self.assertEqual(second.get("/api/profile/picture/").status_code, 404)
        self.assertEqual(second.get(f"/api/profile/picture/?user_id={self.user.pk}").status_code, 404)
        self.assertEqual(second.delete("/api/profile/picture/").status_code, 200)
        self.assertEqual(self.client.get("/api/profile/picture/").status_code, 200)
        self.assertEqual(self.upload(self.image(size=(100, 50)), client=second).status_code, 200)
        self.assertNotEqual(UserProfile.objects.get(user=self.other).picture.name, item.picture.name)
        response = second.get(f"/api/profile/picture/?user_id={self.user.pk}")
        with Image.open(BytesIO(b"".join(response.streaming_content))) as image:
            self.assertEqual(image.size, (100, 50))
        response.close()
        self.assertEqual(UserProfile.objects.get(user=self.user).picture.name, item.picture.name)
        self.assertEqual(second.get(f"/media/{item.picture.name}").status_code, 404)
        self.assertEqual(second.get(f"/api/profile/{self.user.pk}/").status_code, 404)
        self.client.logout()
        for endpoint, methods in (("/api/profile/", ("get", "patch")),
                                  ("/api/profile/picture/", ("get", "post", "delete"))):
            for method in methods:
                response = getattr(self.client, method)(endpoint)
                self.assertEqual(response.status_code, 401)
                self.assertIn("error", response.json())
        self.assertEqual(self.client.get(f"/media/{item.picture.name}").status_code, 404)

    def test_csrf_enforced_for_each_mutation_and_oversized_errors_are_json(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.patch("/api/profile/", "{}", content_type="application/json").status_code, 403)
        self.assertEqual(client.post("/api/profile/picture/", {"picture": self.image()}).status_code, 403)
        self.assertEqual(client.delete("/api/profile/picture/").status_code, 403)
        client.get("/api/session/")
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.patch("/api/profile/", '{"first_name":"CSRF"}',
                                     content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(client.post("/api/profile/picture/", {"picture": self.image()},
                                         HTTP_X_CSRFTOKEN=token).status_code, 200)
            self.assertEqual(client.delete("/api/profile/picture/", HTTP_X_CSRFTOKEN=token).status_code, 200)
        response = client.post("/api/profile/picture/", {
            "picture": SimpleUploadedFile("huge.png", b"x" * (6 * 1024 * 1024 + 1)),
        }, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
