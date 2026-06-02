import asyncio
import importlib
import sys
import types
import unittest


class AccessToken:
    def __init__(self, token, client_id, scopes, expires_at=None):
        self.token = token
        self.client_id = client_id
        self.scopes = scopes
        self.expires_at = expires_at


class TokenVerifier:
    pass


def install_mcp_auth_stubs():
    provider = types.ModuleType("mcp.server.auth.provider")
    provider.AccessToken = AccessToken
    provider.TokenVerifier = TokenVerifier
    sys.modules["mcp"] = types.ModuleType("mcp")
    sys.modules["mcp.server"] = types.ModuleType("mcp.server")
    sys.modules["mcp.server.auth"] = types.ModuleType("mcp.server.auth")
    sys.modules["mcp.server.auth.provider"] = provider


class AuthModeTests(unittest.TestCase):
    def setUp(self):
        install_mcp_auth_stubs()
        sys.modules.pop("auth_config", None)
        self.auth_config = importlib.import_module("auth_config")

    def test_api_key_takes_precedence_over_oauth(self):
        mode = self.auth_config.select_auth_mode(
            mcp_api_key="secret",
            oauth_password="oauth-secret",
        )

        self.assertEqual(mode, "bearer")

    def test_oauth_used_when_api_key_missing(self):
        mode = self.auth_config.select_auth_mode(
            mcp_api_key="",
            oauth_password="oauth-secret",
        )

        self.assertEqual(mode, "oauth")

    def test_missing_auth_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "MCP_API_KEY"):
            self.auth_config.select_auth_mode(mcp_api_key="", oauth_password="")


class StaticBearerTokenVerifierTests(unittest.TestCase):
    def setUp(self):
        install_mcp_auth_stubs()
        sys.modules.pop("auth_config", None)
        self.auth_config = importlib.import_module("auth_config")

    def test_accepts_matching_token(self):
        verifier = self.auth_config.StaticBearerTokenVerifier("secret")

        token = asyncio.run(verifier.verify_token("secret"))

        self.assertIsInstance(token, AccessToken)
        self.assertEqual(token.token, "secret")
        self.assertEqual(token.client_id, "obsidian-mcp")
        self.assertEqual(token.scopes, ["obsidian"])

    def test_rejects_wrong_token(self):
        verifier = self.auth_config.StaticBearerTokenVerifier("secret")

        token = asyncio.run(verifier.verify_token("wrong"))

        self.assertIsNone(token)
