"""
Firebase Auth Dependency
=========================
Verifies Firebase ID tokens without Application Default Credentials
or a service account — works on any machine with internet access.

How it works
------------
Firebase ID tokens are RS256-signed JWTs. We verify them by:
  1. Fetching Firebase's public X.509 certs from a well-known public URL (cached 1 h).
  2. Matching the cert by `kid` from the token header.
  3. Letting PyJWT verify: signature, expiry, audience (project ID), and issuer.

No gcloud login, no service account JSON, no GCP metadata server — none needed.

Usage
-----
  HTTP routes :  Authorization: Bearer <firebase-id-token>
  WebSocket   :  /ws?token=<firebase-id-token>
"""

import os
import time
import logging

import httpx
import jwt as pyjwt
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Load .env early — auth.py may be imported before dashboard.py calls load_dotenv
_env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
load_dotenv(dotenv_path=_env_path)

logger = logging.getLogger("mock_platform")

# ── Config ─────────────────────────────────────────────────────────────────

FIREBASE_PROJECT_ID: str = os.environ.get("FIREBASE_PROJECT_ID", "")

# Firebase's well-known public X.509 signing certificates (no auth required)
_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)

if not FIREBASE_PROJECT_ID:
    logger.warning(
        "⚠️  FIREBASE_PROJECT_ID not set — backend auth verification is DISABLED. "
        "All /admin/* API requests will pass through unauthenticated."
    )
else:
    logger.info(f"✅ Firebase Auth configured for project: {FIREBASE_PROJECT_ID}")

# ── Public cert cache ───────────────────────────────────────────────────────

_cert_cache: dict = {}
_cert_cache_time: float = 0.0
_CERT_TTL: float = 3600.0  # certificates rotate roughly every hour


async def _get_certs() -> dict:
    """
    Fetch and in-process cache Firebase's public X.509 signing certificates.
    Falls back to the existing cached copy if the refresh fails.
    """
    global _cert_cache, _cert_cache_time
    now = time.monotonic()
    if _cert_cache and now - _cert_cache_time < _CERT_TTL:
        return _cert_cache
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_CERTS_URL)
            resp.raise_for_status()
            _cert_cache = resp.json()
            _cert_cache_time = now
            logger.debug("🔑 Firebase public certs refreshed")
    except Exception as exc:
        if _cert_cache:
            logger.warning(f"⚠️  Could not refresh Firebase certs (using cached copy): {exc}")
        else:
            raise RuntimeError(f"Unable to fetch Firebase public certs: {exc}") from exc
    return _cert_cache


# ── Core verification ───────────────────────────────────────────────────────

async def _verify_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token.

    Steps:
      1. Read `kid` from the unverified JWT header.
      2. Fetch Firebase's public X.509 certs (cached).
      3. Extract the RSA public key from the matching cert.
      4. Verify signature, expiry, audience, and issuer via PyJWT.

    Returns the decoded token payload (uid, email, name, …) on success.
    Raises ValueError with a human-readable message on any failure.
    """
    # Step 1 — read header (no signature check yet)
    try:
        header = pyjwt.get_unverified_header(id_token)
    except pyjwt.DecodeError as exc:
        raise ValueError(f"Malformed token: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise ValueError("Token header is missing the 'kid' field")

    # Step 2 — get certs
    certs = await _get_certs()
    if kid not in certs:
        # Could be a just-rotated key — clear cache and retry once
        _cert_cache.clear()
        certs = await _get_certs()
        if kid not in certs:
            raise ValueError(f"No matching public key for kid={kid!r}")

    # Step 3 — load the X.509 certificate and extract the RSA public key
    try:
        cert = x509.load_pem_x509_certificate(certs[kid].encode(), default_backend())
        public_key = cert.public_key()
    except Exception as exc:
        raise ValueError(f"Could not load signing certificate: {exc}") from exc

    # Step 4 — full JWT verification
    try:
        decoded = pyjwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=FIREBASE_PROJECT_ID,
            issuer=f"https://securetoken.google.com/{FIREBASE_PROJECT_ID}",
            options={"verify_exp": True, "verify_iat": True},
        )
    except pyjwt.ExpiredSignatureError:
        raise ValueError("Token has expired — please sign in again")
    except pyjwt.InvalidAudienceError:
        raise ValueError("Token audience does not match this project")
    except pyjwt.InvalidIssuerError:
        raise ValueError("Token issuer is invalid")
    except pyjwt.DecodeError as exc:
        raise ValueError(f"Token signature verification failed: {exc}") from exc

    return decoded


# ── FastAPI Security Scheme ─────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


# ── HTTP dependency ─────────────────────────────────────────────────────────

async def require_auth(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency for HTTP routes.
    Reads 'Authorization: Bearer <token>', verifies it, and returns the
    decoded token payload (uid, email, name, …) on success.

    Dev-mode passthrough: if FIREBASE_PROJECT_ID is unset, returns a
    placeholder identity so the app works locally without Firebase.
    """
    if not FIREBASE_PROJECT_ID:
        return {"uid": "dev", "email": "dev@local", "name": "Dev User"}

    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=401,
            detail=(
                "Authorization header required. "
                "Format: 'Authorization: Bearer <firebase-id-token>'"
            ),
        )

    try:
        return await _verify_token(creds.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        logger.error(f"❌ Auth verification error: {exc}")
        raise HTTPException(status_code=401, detail="Authentication failed.")


# ── WebSocket dependency ────────────────────────────────────────────────────

async def require_auth_ws(token: str = Query(default=None)) -> dict:
    """
    WebSocket-compatible auth dependency.

    Accepts the Firebase ID token as a ?token= query parameter because
    browser WebSocket APIs cannot send custom headers.

    Usage: ws://host/ws?token=<firebase-id-token>
    """
    if not FIREBASE_PROJECT_ID:
        return {"uid": "dev", "email": "dev@local", "name": "Dev User"}

    if not token:
        raise HTTPException(
            status_code=401,
            detail="WebSocket connection requires ?token=<firebase-id-token>",
        )

    try:
        return await _verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        logger.error(f"❌ WebSocket auth error: {exc}")
        raise HTTPException(status_code=401, detail="Invalid token.")
