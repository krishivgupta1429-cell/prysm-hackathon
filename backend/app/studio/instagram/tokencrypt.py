"""Built during the Meta Global AI Developer Hackathon (new work).

Encryption at rest for the Instagram access token (users.instagram_access_token).

* Fernet (AES-128-CBC + HMAC) with the key in TOKEN_ENCRYPTION_KEY. Several keys may be listed, comma separated: the first one
  encrypts, all of them decrypt (key rotation).
* Stored form: "enc:v1:<fernet token>". A value without that prefix is a plain token written before encryption existed; it is
  still read as is (transparent), and the migration / the first use re-saves it encrypted.
* No key set: development keeps working with a plain token (one warning, no secret in it). In production / staging the token is
  NOT stored (TokenCryptoError), so a missing key can never silently leave tokens in plain text.
* Reading fails soft: a token that cannot be decrypted (key missing or changed) reads as None, i.e. "not connected, connect
  again". It never breaks loading the user row.
* Neither the key nor any token is ever logged or put in an exception message.

Generate a key:  python -m app.studio.instagram.tokencrypt generate
"""
import logging
from functools import lru_cache
from typing import Optional

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.studio import config

log = logging.getLogger(__name__)
PREFIX = "enc:v1:"
_warned = False


class TokenCryptoError(Exception):
    """Safe message only: never contains the key or a token."""


def _keys_raw() -> str | None:
    return config.get_env("TOKEN_ENCRYPTION_KEY")


@lru_cache(maxsize=8)
def _fernet(keys: str):
    from cryptography.fernet import Fernet, MultiFernet
    try:
        parts = [Fernet(k.strip().encode()) for k in keys.split(",") if k.strip()]
    except Exception:   # wrong length / not url-safe base64: say so without echoing the value
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes).") from None
    if not parts:
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is empty.")
    return MultiFernet(parts)


def key_configured() -> bool:
    return bool(_keys_raw())


def is_encrypted(stored: Optional[str]) -> bool:
    return bool(stored) and str(stored).startswith(PREFIX)


def _production() -> bool:
    try:
        from app.core.config import settings
        return str(getattr(settings, "APP_ENV", "")).lower() in ("production", "prod", "staging")
    except Exception:
        return False


def encrypt(plain: Optional[str]) -> Optional[str]:
    if plain is None or plain == "":
        return plain
    if is_encrypted(plain):          # already encrypted: never wrap twice
        return plain
    keys = _keys_raw()
    if not keys:
        if _production():
            raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not set: refusing to store the Instagram token in plain text.")
        global _warned
        if not _warned:
            _warned = True
            log.warning("[Instagram] TOKEN_ENCRYPTION_KEY is not set: the access token is stored in plain text (development only).")
        return plain
    return PREFIX + _fernet(keys).encrypt(plain.encode()).decode()


def decrypt(stored: Optional[str]) -> Optional[str]:
    """The plain token. A plain (legacy) value is returned unchanged. Raises TokenCryptoError when it cannot be decrypted."""
    if stored is None or stored == "" or not is_encrypted(stored):
        return stored
    keys = _keys_raw()
    if not keys:
        raise TokenCryptoError("The stored Instagram token is encrypted but TOKEN_ENCRYPTION_KEY is not set.")
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(keys).decrypt(stored[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise TokenCryptoError("The stored Instagram token could not be decrypted with TOKEN_ENCRYPTION_KEY.") from None


class EncryptedToken(TypeDecorator):
    """Column type for users.instagram_access_token: encrypts on write, decrypts on read, everywhere (also in baseline code)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        try:
            return decrypt(value)
        except TokenCryptoError as e:
            log.warning("[Instagram] %s The creator has to connect Instagram again.", e)
            return None


def startup_check() -> None:
    """Called once when the server starts. In production / staging the server refuses to start without a valid TOKEN_ENCRYPTION_KEY
    (and with the dev-only fixture mode on), so Instagram tokens can never be saved in plain text by mistake. Silent in development."""
    if not _production():
        return
    keys = _keys_raw()
    if not keys:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set in production (it encrypts the Instagram access token). "
                           "Create one with: python -m app.studio.instagram.tokencrypt generate")
    try:
        _fernet(keys)
    except TokenCryptoError as e:
        raise RuntimeError(str(e)) from None
    if (config.get_env("INSTAGRAM_FIXTURE") or "") == "1":
        raise RuntimeError("INSTAGRAM_FIXTURE=1 (fake Instagram data) must not be set in production.")


def encrypt_existing(connection) -> int:
    """Re-save every plain token as encrypted (sync connection, used by the migration). Returns how many rows changed.
    Needs TOKEN_ENCRYPTION_KEY; raises TokenCryptoError without it."""
    from sqlalchemy import text
    if not key_configured():
        raise TokenCryptoError("TOKEN_ENCRYPTION_KEY is not set.")
    rows = connection.execute(text(
        "SELECT id, instagram_access_token FROM users "
        "WHERE instagram_access_token IS NOT NULL AND instagram_access_token <> '' AND instagram_access_token NOT LIKE :p"),
        {"p": PREFIX + "%"}).fetchall()
    for uid, tok in rows:
        connection.execute(text("UPDATE users SET instagram_access_token = :t WHERE id = :i"), {"t": encrypt(tok), "i": uid})
    return len(rows)


def decrypt_existing(connection) -> int:
    """The reverse (migration downgrade): back to plain text."""
    from sqlalchemy import text
    rows = connection.execute(text(
        "SELECT id, instagram_access_token FROM users WHERE instagram_access_token LIKE :p"), {"p": PREFIX + "%"}).fetchall()
    for uid, tok in rows:
        connection.execute(text("UPDATE users SET instagram_access_token = :t WHERE id = :i"), {"t": decrypt(tok), "i": uid})
    return len(rows)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["generate"]:
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())   # a NEW key, not an existing secret
    else:
        print("usage: python -m app.studio.instagram.tokencrypt generate")
