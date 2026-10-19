"""Authentication handler for GreenNode MCP Server."""

from __future__ import annotations

from greennode.mcp_core.http import user_token_var
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.config import VksConfig
from greennode.vks_mcp_server.tool_annotations import READ


class AuthHandler:
    """Register and serve authentication-related MCP tools."""

    def __init__(self, mcp, config: VksConfig, token_manager: TokenManager):
        self.mcp = mcp
        self.config = config
        self.token_manager = token_manager
        self.mcp.tool(name="get_access_token", annotations=READ)(self.get_access_token)

    async def get_access_token(self) -> str:
        """Checks authentication for VKS/vServer API calls. Never returns the token itself.

        Reports which identity the calls run as (the caller's own IAM token, or the
        server's service account), the default region and the endpoint URLs.
        """
        region = self.config.default_region
        endpoints = self.config.get_endpoints(region)
        # A caller token is validated by the API on every call; the service
        # account must never be consulted (let alone revealed) on its behalf.
        if user_token_var.get():
            auth_mode = "caller IAM token (per-request passthrough, validated by the API)"
        else:
            await self.token_manager.get_token()  # fails loudly on bad credentials
            auth_mode = "service account (client credentials, auto-refresh)"

        return (
            "access_token: not returned (tokens are never exposed through tools)"
            f"\nauth_mode: {auth_mode}"
            f"\nregion: {region}"
            f"\nvks_endpoint: {endpoints.vks}"
            f"\nvserver_endpoint: {endpoints.vserver}"
        )
