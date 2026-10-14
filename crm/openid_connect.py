"""OpenID Connect support for Microsoft's tenant-independent organizations issuer."""
from uuid import UUID

import jwt
from allauth.socialaccount.internal import jwtkit
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.openid_connect.provider import OpenIDConnectProvider
from allauth.socialaccount.providers.openid_connect.views import OpenIDConnectOAuth2Adapter


class TenantAwareOpenIDConnectOAuth2Adapter(OpenIDConnectOAuth2Adapter):
    def _decode_id_token(self, app, id_token):
        provider_settings = self.get_provider().app.settings
        if provider_settings.get("graph_tenant_id") != "organizations":
            return super()._decode_id_token(app, id_token)

        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError
            unverified = jwt.decode(
                id_token, options={"verify_signature": False, "verify_exp": False},
            )
            tid = unverified.get("tid")
            if not isinstance(tid, str):
                raise ValueError
            tenant_id = str(UUID(tid))
            claims = jwtkit.verify_and_decode(
                credential=id_token,
                keys_url=self.openid_config["jwks_uri"],
                issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
                audience=app.client_id,
                lookup_kid=jwtkit.lookup_kid_jwk,
                verify_signature=not self.did_fetch_access_token,
            )
            if claims.get("tid") != tid:
                raise ValueError
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise OAuth2Error("Invalid id_token") from exc


class TenantAwareOpenIDConnectProvider(OpenIDConnectProvider):
    package = "allauth.socialaccount.providers.openid_connect"
    oauth2_adapter_class = TenantAwareOpenIDConnectOAuth2Adapter
