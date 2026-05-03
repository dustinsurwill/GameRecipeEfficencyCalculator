"""
OAuth provider definitions using Authlib.

Each provider is a named config dict that the OAuth router uses to:
  1. Redirect the user to the provider's login page
  2. Exchange the callback code for tokens
  3. Fetch the user's profile from the provider API

Adding a new provider = add an entry here + a profile-fetching function.
No changes needed in the router.
"""
from dataclasses import dataclass

from authlib.integrations.httpx_client import AsyncOAuth2Client

from app.config import get_settings

settings = get_settings()


@dataclass
class OAuthProviderProfile:
    """Normalised user info returned by any provider."""
    provider: str
    provider_user_id: str       # immutable ID from that provider
    provider_email: str | None  # email on their account with THIS provider
    display_name: str | None
    avatar_url: str | None
    access_token: str
    refresh_token: str | None
    token_expires_at: int | None  # unix timestamp, if provided


# ── Provider configs ──────────────────────────────────────────────────────────

def _github_client() -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        scope="read:user user:email",
    )


def _google_client() -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scope="openid email profile",
    )


def _authentik_client() -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        client_id=settings.authentik_client_id,
        client_secret=settings.authentik_client_secret,
        scope="openid email profile",
    )


# Provider metadata: authorize URL, token URL, profile fetcher
PROVIDER_META: dict[str, dict] = {
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "client_factory": _github_client,
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_factory": _google_client,
    },
    # Authentik only registered if base_url is configured
    **(
        {
            "authentik": {
                "authorize_url": f"{settings.authentik_base_url}/application/o/authorize/",
                "token_url": f"{settings.authentik_base_url}/application/o/token/",
                "client_factory": _authentik_client,
            }
        }
        if settings.authentik_base_url
        else {}
    ),
}


def get_enabled_providers() -> list[str]:
    enabled = []
    if settings.github_client_id:
        enabled.append("github")
    if settings.google_client_id:
        enabled.append("google")
    if settings.authentik_base_url and settings.authentik_client_id:
        enabled.append("authentik")
    return enabled


# ── Profile fetchers ──────────────────────────────────────────────────────────

async def fetch_github_profile(
    client: AsyncOAuth2Client, token: dict
) -> OAuthProviderProfile:
    resp = await client.get("https://api.github.com/user")
    resp.raise_for_status()
    data = resp.json()

    # GitHub may not expose email in the user object — fetch separately
    email: str | None = data.get("email")
    if not email:
        email_resp = await client.get("https://api.github.com/user/emails")
        if email_resp.is_success:
            emails = email_resp.json()
            primary = next(
                (e for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            email = primary["email"] if primary else None

    return OAuthProviderProfile(
        provider="github",
        provider_user_id=str(data["id"]),
        provider_email=email,
        display_name=data.get("name") or data.get("login"),
        avatar_url=data.get("avatar_url"),
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        token_expires_at=token.get("expires_at"),
    )


async def fetch_google_profile(
    client: AsyncOAuth2Client, token: dict
) -> OAuthProviderProfile:
    resp = await client.get("https://www.googleapis.com/oauth2/v2/userinfo")
    resp.raise_for_status()
    data = resp.json()

    return OAuthProviderProfile(
        provider="google",
        provider_user_id=data["id"],
        provider_email=data.get("email"),
        display_name=data.get("name"),
        avatar_url=data.get("picture"),
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        token_expires_at=token.get("expires_at"),
    )


async def fetch_authentik_profile(
    client: AsyncOAuth2Client, token: dict
) -> OAuthProviderProfile:
    userinfo_url = f"{settings.authentik_base_url}/application/o/userinfo/"
    resp = await client.get(userinfo_url)
    resp.raise_for_status()
    data = resp.json()

    return OAuthProviderProfile(
        provider="authentik",
        provider_user_id=data["sub"],
        provider_email=data.get("email"),
        display_name=data.get("name"),
        avatar_url=data.get("picture"),
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        token_expires_at=token.get("expires_at"),
    )


PROFILE_FETCHERS = {
    "github": fetch_github_profile,
    "google": fetch_google_profile,
    "authentik": fetch_authentik_profile,
}
