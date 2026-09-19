"""Password hashing and bearer tokens (spec §31).

Hashing uses ``hashlib.scrypt`` from the standard library. A memory-hard
function is the requirement; taking it from the standard library rather than a
third-party wrapper removes a dependency from the one place in the codebase
where a supply-chain compromise would be worst.

Parameters follow the interactive-login profile from the scrypt paper
(N=2^15, r=8, p=1), which costs roughly 32 MiB per verification — enough to
make large-scale offline cracking expensive without making a login slow.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt

from app.core.config import Settings

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16

_ALGORITHM = "HS256"
_PREFIX = "scrypt"


class InvalidTokenError(ValueError):
    """The bearer token is missing, malformed, or expired."""


def hash_password(password: str) -> str:
    """Return a self-describing hash: ``scrypt$N$r$p$salt$key``.

    The parameters travel with the hash so they can be raised later without
    invalidating existing passwords.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_N * SCRYPT_R * 200,
    )
    return "$".join(
        [
            _PREFIX,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        ]
    )


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real hash to verify against when no account exists.

    Without it, a login for an unregistered address returns before doing any
    scrypt work, and the response is measurably faster than one for a
    registered address with the wrong password. That difference enumerates who
    has an account.
    """
    return hash_password(secrets.token_urlsafe(32))


def verify_password_constant_work(password: str, encoded: str | None) -> bool:
    """Verify, doing the same work whether or not a hash was supplied.

    Use this on the sign-in path. ``verify_password`` short-circuits on a
    missing hash, which is right for callers that already know the account
    exists and wrong for the one that does not.
    """
    if encoded is None:
        verify_password(password, _dummy_hash())
        return False
    return verify_password(password, encoded)


def verify_password(password: str, encoded: str | None) -> bool:
    """Check a password against a stored hash.

    Returns False rather than raising for a malformed hash. Note that this
    short-circuits when ``encoded`` is ``None``; the sign-in path must use
    ``verify_password_constant_work`` instead.
    """
    if not encoded:
        return False
    try:
        prefix, n, r, p, salt_b64, key_b64 = encoded.split("$")
        if prefix != _PREFIX:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=int(n) * int(r) * 200,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    expires_at: dt.datetime


def issue_token(
    user_id: uuid.UUID,
    settings: Settings,
    *,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    now = now or dt.datetime.now(dt.UTC)
    expires_at = now + dt.timedelta(minutes=settings.auth.token_ttl_minutes)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        settings.auth.secret_key,
        algorithm=_ALGORITHM,
    )
    return token, expires_at


def read_token(token: str, settings: Settings) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.auth.secret_key,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            expires_at=dt.datetime.fromtimestamp(payload["exp"], tz=dt.UTC),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc
