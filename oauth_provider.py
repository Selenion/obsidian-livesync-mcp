"""Simple OAuth 2.1 Authorization Server Provider for MCP."""

import secrets
import time
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@dataclass
class SimpleAuthCode:
    code: str
    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    scopes: list[str]
    expires_at: float


@dataclass
class SimpleAccessToken:
    token: str
    client_id: str
    scopes: list[str]
    expires_at: float


@dataclass
class SimpleRefreshToken:
    token: str
    client_id: str
    scopes: list[str]
    expires_at: float


class SimpleOAuthProvider(
    OAuthAuthorizationServerProvider[SimpleAuthCode, SimpleAccessToken, SimpleRefreshToken]
):
    """In-memory OAuth provider with simple browser-based consent."""

    def __init__(
        self,
        server_url: str,
        access_password: str,
        client_id: str = "",
        client_secret: str = "",
    ):
        self.server_url = server_url.rstrip("/")
        self.access_password = access_password
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.auth_codes: dict[str, SimpleAuthCode] = {}
        self.access_tokens: dict[str, SimpleAccessToken] = {}
        self.refresh_tokens: dict[str, SimpleRefreshToken] = {}

        # Pre-register fixed client
        if client_id and client_secret:
            self.clients[client_id] = OAuthClientInformationFull(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"],
                client_name="Claude",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="client_secret_post",
                scope="claudeai",
            )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # Auto-approve: generate authorization code immediately
        # The password check happens at the /authorize page level
        code = secrets.token_hex(32)
        redirect_uri_str = str(params.redirect_uri)
        self.auth_codes[code] = SimpleAuthCode(
            code=code,
            client_id=client.client_id,
            redirect_uri=redirect_uri_str,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
        )
        return construct_redirect_uri(
            redirect_uri_str, code=code, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> SimpleAuthCode | None:
        ac = self.auth_codes.get(authorization_code)
        if ac and ac.client_id == client.client_id:
            if time.time() > ac.expires_at:
                del self.auth_codes[authorization_code]
                return None
            return ac
        return None

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: SimpleAuthCode,
    ) -> OAuthToken:
        self.auth_codes.pop(authorization_code.code, None)

        access_token = secrets.token_hex(32)
        refresh_token = secrets.token_hex(32)

        self.access_tokens[access_token] = SimpleAccessToken(
            token=access_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=time.time() + 86400,
        )
        self.refresh_tokens[refresh_token] = SimpleRefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=time.time() + 86400 * 30,
        )

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=86400,
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_access_token(self, token: str) -> SimpleAccessToken | None:
        at = self.access_tokens.get(token)
        if at and time.time() > at.expires_at:
            del self.access_tokens[token]
            return None
        return at

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> SimpleRefreshToken | None:
        rt = self.refresh_tokens.get(refresh_token)
        if rt and rt.client_id == client.client_id:
            if time.time() > rt.expires_at:
                del self.refresh_tokens[refresh_token]
                return None
            return rt
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: SimpleRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self.refresh_tokens.pop(refresh_token.token, None)

        new_access_token = secrets.token_hex(32)
        new_refresh_token = secrets.token_hex(32)
        effective_scopes = scopes or refresh_token.scopes

        self.access_tokens[new_access_token] = SimpleAccessToken(
            token=new_access_token,
            client_id=client.client_id,
            scopes=effective_scopes,
            expires_at=time.time() + 86400,
        )
        self.refresh_tokens[new_refresh_token] = SimpleRefreshToken(
            token=new_refresh_token,
            client_id=client.client_id,
            scopes=effective_scopes,
            expires_at=time.time() + 86400 * 30,
        )

        return OAuthToken(
            access_token=new_access_token,
            token_type="Bearer",
            expires_in=86400,
            refresh_token=new_refresh_token,
            scope=" ".join(effective_scopes) if effective_scopes else None,
        )

    async def revoke_token(
        self, token: SimpleAccessToken | SimpleRefreshToken
    ) -> None:
        if isinstance(token, SimpleAccessToken):
            self.access_tokens.pop(token.token, None)
        elif isinstance(token, SimpleRefreshToken):
            self.refresh_tokens.pop(token.token, None)
