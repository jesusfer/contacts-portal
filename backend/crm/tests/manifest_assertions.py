def assert_sso_resource_is_valid(test_case, manifest):
    test_case.assertEqual(manifest["webApplicationInfo"]["id"], "<TEAMS_SSO_APP_ID>")
    test_case.assertIn(
        manifest["webApplicationInfo"]["resource"],
        (
            "api://<PORTAL_HOST>/<TEAMS_SSO_APP_ID>",
            "api://<PORTAL_HOST>/botid-<TEAMS_SSO_APP_ID>",
        ),
    )
