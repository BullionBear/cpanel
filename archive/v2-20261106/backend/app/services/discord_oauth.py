"""Discord OAuth2 helpers (same flow as the archived backend)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"


@dataclass
class DiscordUser:
    id: str
    username: str
    email: Optional[str]
    global_name: Optional[str]

    @property
    def display_name(self) -> str:
        return self.global_name or self.username


class DiscordOAuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.DISCORD_CLIENT_ID,
        "redirect_uri": settings.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email",
        "state": state,
        "prompt": "consent",
    }
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data={
                "client_id": settings.DISCORD_CLIENT_ID,
                "client_secret": settings.DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise DiscordOAuthError("Failed to exchange Discord authorization code", 401)
        return response.json()


async def fetch_current_user(access_token: str) -> DiscordUser:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            raise DiscordOAuthError("Failed to fetch Discord user profile", 401)
        data = response.json()
        return DiscordUser(
            id=str(data["id"]),
            username=data.get("username", f"user_{data['id']}"),
            email=data.get("email"),
            global_name=data.get("global_name"),
        )


async def verify_guild_membership(discord_user_id: str) -> None:
    if not settings.DISCORD_GUILD_ID:
        raise DiscordOAuthError("Discord guild is not configured", 500)

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{DISCORD_API_BASE}/guilds/{settings.DISCORD_GUILD_ID}/members/{discord_user_id}",
            headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"},
        )
        if response.status_code == 404:
            raise DiscordOAuthError("You must be a member of the required Discord server", 403)
        if response.status_code != 200:
            raise DiscordOAuthError("Failed to verify Discord guild membership", 403)

        if settings.DISCORD_REQUIRED_ROLE_ID:
            member = response.json()
            roles = member.get("roles", [])
            if settings.DISCORD_REQUIRED_ROLE_ID not in roles:
                raise DiscordOAuthError("You do not have the required Discord role", 403)
