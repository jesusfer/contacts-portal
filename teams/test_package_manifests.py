import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from package_manifests import PLACEHOLDER, package_manifests, read_env, render


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.teams_dir = Path(self.temporary.name)
        source = Path(__file__).resolve().parent
        for name in ("api-extension", "tabs-bot-extension"):
            shutil.copytree(source / name, self.teams_dir / name)
        self.values = {
            "PORTAL_HOST": "portal.example.com",
            "TEAMS_APP_ID": "11111111-1111-1111-1111-111111111111",
            "TEAMS_API_APP_ID": "22222222-2222-2222-2222-222222222222",
            "TEAMS_SSO_APP_ID": "33333333-3333-3333-3333-333333333333",
            "TEAMS_BOT_APP_ID": "44444444-4444-4444-4444-444444444444",
            "TEAMS_OIDC_PROVIDER_ID": "entraid-work",
        }
        self.env = self.teams_dir / "teams.env"
        self.env.write_text(
            "\n".join(f"{key}={value}" for key, value in self.values.items()),
            encoding="utf-8",
        )

    def test_packages_resources_and_preserves_sources(self):
        manifest = self.teams_dir / "api-extension" / "api-manifest.json"
        original = manifest.read_bytes()
        packages = package_manifests(self.teams_dir)
        self.assertEqual(
            {path.name for path in packages},
            {
                "api-extension.zip",
                "tabs-bot-extension.zip",
            },
        )
        for package in packages:
            with ZipFile(package) as archive:
                self.assertIn("manifest.json", archive.namelist())
                self.assertNotIn("api-manifest.json", archive.namelist())
                for name in ("color.png", "outline.png"):
                    self.assertEqual(
                        archive.read(name),
                        (self.teams_dir / package.stem / name).read_bytes(),
                    )
                for name in archive.namelist():
                    if name.endswith(".json"):
                        content = archive.read(name).decode("utf-8")
                        json.loads(content)
                        self.assertNotIn("<PORTAL_HOST>", content)
                data = json.loads(archive.read("manifest.json"))
                self.assertEqual(data["validDomains"], ["portal.example.com"])
                if package.stem == "api-extension":
                    self.assertEqual(data["id"], self.values["TEAMS_API_APP_ID"])
                    specification = json.loads(
                        archive.read("contact-search-openapi.json")
                    )
                    self.assertEqual(
                        specification["servers"][0]["url"], "https://portal.example.com"
                    )
                    self.assertIn(
                        "${name}", archive.read("contact-card-template.json").decode()
                    )
                else:
                    self.assertEqual(data["id"], self.values["TEAMS_APP_ID"])
                    self.assertIn(
                        "?provider=entraid-work", data["staticTabs"][0]["contentUrl"]
                    )
        self.assertEqual(manifest.read_bytes(), original)

    def test_missing_or_blank_values_do_not_write_archives(self):
        for value in ("", "<TEAMS_APP_ID>"):
            with self.subTest(value=value):
                values = {**self.values, "TEAMS_APP_ID": value}
                self.env.write_text(
                    "\n".join(f"{key}={item}" for key, item in values.items()),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "TEAMS_APP_ID"):
                    package_manifests(self.teams_dir)
                self.assertFalse((self.teams_dir / "dist").exists())
        self.env.write_text("PORTAL_HOST=portal.example.com\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "TEAMS_API_APP_ID"):
            package_manifests(self.teams_dir)

    def test_missing_env_file(self):
        self.env.unlink()
        with self.assertRaises(FileNotFoundError):
            package_manifests(self.teams_dir)

    def test_env_comments_quotes_and_literal_values(self):
        self.env.write_text(
            "# Comment\n\nPORTAL_HOST = 'portal.example.com'\n"
            'TEAMS_OIDC_PROVIDER_ID="value=with#symbols"\n',
            encoding="utf-8",
        )
        self.assertEqual(
            read_env(self.env),
            {
                "PORTAL_HOST": "portal.example.com",
                "TEAMS_OIDC_PROVIDER_ID": "value=with#symbols",
            },
        )

    def test_malformed_env(self):
        for line in ("invalid", "invalid=value", 'KEY="unclosed'):
            with self.subTest(line=line):
                self.env.write_text(line, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_env(self.env)

    def test_json_values_are_safely_escaped(self):
        value = 'quoted "value" with \\ and \n'
        rendered = render({"value": "<VALUE>", "number": 1}, {"VALUE": value})
        self.assertEqual(
            json.loads(json.dumps(rendered)), {"value": value, "number": 1}
        )

    def test_rerun_replaces_archives(self):
        package_manifests(self.teams_dir)
        self.env.write_text(
            self.env.read_text().replace(
                "portal.example.com",
                "new.example.com",
            ),
            encoding="utf-8",
        )
        for package in package_manifests(self.teams_dir):
            with ZipFile(package) as archive:
                self.assertEqual(
                    json.loads(archive.read("manifest.json"))["validDomains"],
                    ["new.example.com"],
                )

    def test_example_covers_all_placeholders(self):
        source = Path(__file__).resolve().parent
        placeholders = {
            key
            for template in source.glob("*/*.json")
            for key in PLACEHOLDER.findall(template.read_text(encoding="utf-8"))
        }
        self.assertEqual(set(read_env(source / "teams.env.example")), placeholders)

    def test_invalid_json_does_not_replace_existing_archives(self):
        packages = package_manifests(self.teams_dir)
        originals = {path: path.read_bytes() for path in packages}
        (self.teams_dir / "tabs-bot-extension" / "manifest.json").write_text(
            "{invalid",
            encoding="utf-8",
        )
        with self.assertRaises(json.JSONDecodeError):
            package_manifests(self.teams_dir)
        self.assertEqual({path: path.read_bytes() for path in packages}, originals)


if __name__ == "__main__":
    unittest.main()
