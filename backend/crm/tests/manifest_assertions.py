def assert_sso_resource_matches_manifest_features(test_case, manifest):
    uses_bot_framework = bool(manifest.get("bots")) or any(
        "botId" in extension for extension in manifest.get("composeExtensions", [])
    )
    resource_path = (
        "botid-<TEAMS_SSO_APP_ID>"
        if uses_bot_framework
        else "<TEAMS_SSO_APP_ID>"
    )
    test_case.assertEqual(
        manifest["webApplicationInfo"],
        {
            "id": "<TEAMS_SSO_APP_ID>",
            "resource": f"api://<PORTAL_HOST>/{resource_path}",
        },
    )
