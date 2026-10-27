from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    origin: str
    owner_email: str
    aad_id: str = ""
    google_id: str = ""
    app_service: bool = False
    microsoft_login_enabled: bool = False
    google_login_enabled: bool = False

    @classmethod
    def environment(cls):
        origin = os.environ.get("APP_ORIGIN", "").rstrip("/")
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("APP_ORIGIN must be the canonical HTTPS origin")
        return cls(
            origin=origin,
            owner_email=os.environ.get("OWNER_EMAIL", "").strip().casefold(),
            aad_id=os.environ.get("OWNER_AAD_ID", ""),
            google_id=os.environ.get("OWNER_GOOGLE_ID", ""),
            app_service=bool(os.environ.get("WEBSITE_SITE_NAME"))
            and os.environ.get("WEBSITE_AUTH_ENABLED", "").lower() == "true",
            microsoft_login_enabled=os.environ.get("MICROSOFT_LOGIN_ENABLED", "").lower() == "true",
            google_login_enabled=os.environ.get("GOOGLE_LOGIN_ENABLED", "").lower() == "true",
        )


@dataclass(frozen=True)
class Identity:
    provider: str
    identifier: str
    email: str

    @property
    def actor(self):
        return f"{self.provider}:{self.identifier}"


def authenticated(request: Request) -> Identity:
    settings = request.app.state.settings
    # These headers are trustworthy only behind Azure's enabled Easy Auth gateway.
    if not settings.app_service:
        raise HTTPException(503, "App Service authentication is not configured")
    raw = request.headers.get("x-ms-client-principal", "")
    if not raw:
        raise HTTPException(401, "Sign in to continue")
    try:
        if len(raw) > 32768:
            raise ValueError("Oversized identity")
        principal = json.loads(base64.b64decode(raw, validate=True))
        provider = principal["auth_typ"]
        identifier = request.headers["x-ms-client-principal-id"]
        email = request.headers["x-ms-client-principal-name"].strip().casefold()
        if (
            provider not in {"aad", "google"}
            or request.headers.get("x-ms-client-principal-idp") != provider
            or not identifier
            or len(identifier) > 512
            or not email
            or not isinstance(principal.get("claims"), list)
        ):
            raise ValueError("Incomplete identity")
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as error:
        logger.warning("Rejected malformed gateway identity")
        raise HTTPException(401, "Invalid authentication context") from error
    return Identity(provider, identifier, email)


def owner(request: Request) -> Identity:
    identity = authenticated(request)
    settings = request.app.state.settings
    allowed = settings.aad_id if identity.provider == "aad" else settings.google_id
    if (
        not settings.owner_email
        or identity.email != settings.owner_email
        or not allowed
        or identity.identifier != allowed
    ):
        logger.warning("Rejected an identity outside the private owner allowlist")
        raise HTTPException(403, "This account has not been granted project access")
    return identity


def mutation(request: Request) -> Identity:
    identity = owner(request)
    if (
        not request.app.state.settings.origin
        or request.headers.get("origin") != request.app.state.settings.origin
        or request.headers.get("x-requested-with") != "RemodelTracker"
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
    ):
        raise HTTPException(403, "A same-origin app request is required")
    return identity
