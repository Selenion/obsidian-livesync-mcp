"""Authentication configuration helpers for the Obsidian MCP server."""

import hmac

from mcp.server.auth.provider import AccessToken, TokenVerifier


def select_auth_mode(mcp_api_key: str, oauth_password: str) -> str:
    """Return the configured auth mode, failing closed when auth is absent."""
    if mcp_api_key:
        return "bearer"
    if oauth_password:
        return "oauth"
    raise RuntimeError(
        "Authentication is required. Set MCP_API_KEY for bearer auth or "
        "OAUTH_PASSWORD for OAuth."
    )


class StaticBearerTokenVerifier(TokenVerifier):
    """Validate a single shared bearer token from MCP_API_KEY."""

    def __init__(self, expected_token: str):
        if not expected_token:
            raise ValueError("expected_token must not be empty")
        self.expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not hmac.compare_digest(token, self.expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="obsidian-mcp",
            scopes=["obsidian"],
            expires_at=None,
        )
