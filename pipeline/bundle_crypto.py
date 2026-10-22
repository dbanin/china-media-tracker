"""Authenticated encryption of the relay bundle (scratchpad DECISIONS 7), standard library only.

Retired with the relay on 2026-10-22: nothing on the runner seals or opens a bundle, and the
RELAY_BUNDLE_KEY secret is never to be added. Kept for the tests and the record.

The relay branch is public, so a bundle that carries article bodies must not be readable there.
The bundle is sealed with a secret that only the relay machine and the runner hold: RELAY_BUNDLE_KEY
in the environment (an Actions secret on the runner), or on the relay machine a file named by
RELAY_BUNDLE_KEY_FILE (scripts/relay.sh). The secret is never written anywhere by this code, never
logged and never put in the bundle.

Why this construction. No AEAD library is installed (the cryptography package is absent and the
relay must not install one over the network), so the bundle is sealed with encrypt then MAC from
hashlib and hmac:
  - two keys are derived from the secret with HMAC-SHA256 under distinct labels, one to encrypt
    and one to authenticate, so the two never share a key;
  - the keystream is SHAKE256(encryption key || nonce || block counter), a keyed sponge, which is
    a pseudorandom function (the construction KMAC, NIST SP 800-185, rests on), used in counter
    mode with a fresh random 24 byte nonce per bundle, so no keystream is ever reused;
  - the tag is HMAC-SHA256(authentication key, magic || nonce || ciphertext), checked in constant
    time before anything is decrypted, so a changed or truncated bundle is refused whole.
Encrypt then MAC with a PRF in counter mode and a PRF as MAC is the generic composition that gives
authenticated encryption. When the owner adds an AEAD library, a format with a new magic can
replace this one; the magic says which format a bundle is in.

Format: MAGIC (7 bytes) | nonce (24) | ciphertext (the gzipped JSON lines bundle) | tag (32).
"""
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path
from typing import Mapping, Optional

MAGIC = b"CMTRB1\n"
NONCE_BYTES = 24
TAG_BYTES = 32
CHUNK_BYTES = 1 << 20
KEY_ENV = "RELAY_BUNDLE_KEY"
KEY_FILE_ENV = "RELAY_BUNDLE_KEY_FILE"
# A secret shorter than this is refused as too weak: 32 characters of secrets.token_urlsafe carry
# about 190 bits. Generate one with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
MIN_KEY_CHARS = 32
_LABEL_ENC = b"china-media-tracker relay bundle v1 encryption"
_LABEL_MAC = b"china-media-tracker relay bundle v1 authentication"


class BundleKeyError(Exception):
    """The bundle is sealed and no usable key is configured."""


class BundleAuthError(Exception):
    """The bundle is sealed but its tag does not verify: a wrong key, or a changed or cut bundle."""


def _warn(msg: str) -> None:
    print("relay bundle: %s" % msg, file=sys.stderr)


def load_key(environ: Optional[Mapping[str, str]] = None) -> Optional[bytes]:
    """The secret from RELAY_BUNDLE_KEY, or else from the file RELAY_BUNDLE_KEY_FILE names, or None
    when neither gives one. A key shorter than MIN_KEY_CHARS counts as none, with a warning (the
    warning never shows the key). Surrounding whitespace, a file's final newline included, is ignored."""
    env = os.environ if environ is None else environ
    raw = (env.get(KEY_ENV) or "").strip()
    if not raw:
        path = (env.get(KEY_FILE_ENV) or "").strip()
        if path:
            p = Path(path).expanduser()
            try:
                raw = p.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                return None
            except OSError as exc:
                _warn("the key file could not be read (%s); bodies are left out" % type(exc).__name__)
                return None
            try:
                if p.stat().st_mode & 0o077:
                    _warn("the key file can be read by other users; chmod 600 it")
            except OSError:
                pass
    if not raw:
        return None
    if len(raw) < MIN_KEY_CHARS:
        _warn("the key is shorter than %d characters and is not used" % MIN_KEY_CHARS)
        return None
    return raw.encode("utf-8")


def _subkeys(secret: bytes):
    return (hmac.new(secret, _LABEL_ENC, hashlib.sha256).digest(),
            hmac.new(secret, _LABEL_MAC, hashlib.sha256).digest())


def _xor_stream(enc_key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    for i, off in enumerate(range(0, len(data), CHUNK_BYTES)):
        block = data[off:off + CHUNK_BYTES]
        ks = hashlib.shake_256(enc_key + nonce + i.to_bytes(8, "big")).digest(len(block))
        out += (int.from_bytes(block, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(block), "big")
    return bytes(out)


def is_sealed(blob: Optional[bytes]) -> bool:
    return bool(blob) and blob.startswith(MAGIC)


def seal(data: bytes, secret: bytes) -> bytes:
    """data encrypted and authenticated under secret (see the module notes)."""
    enc_key, mac_key = _subkeys(secret)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = _xor_stream(enc_key, nonce, data)
    tag = hmac.new(mac_key, MAGIC + nonce + ct, hashlib.sha256).digest()
    return MAGIC + nonce + ct + tag


def open_sealed(blob: bytes, secret: Optional[bytes]) -> bytes:
    """The bundle inside a sealed blob. Raises BundleKeyError without a key, BundleAuthError when
    the tag does not verify; nothing is decrypted before the tag has been checked."""
    if not is_sealed(blob):
        raise BundleAuthError("not a sealed bundle")
    if not secret:
        raise BundleKeyError("the bundle is sealed and %s is not set" % KEY_ENV)
    if len(blob) < len(MAGIC) + NONCE_BYTES + TAG_BYTES:
        raise BundleAuthError("sealed bundle too short")
    enc_key, mac_key = _subkeys(secret)
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    ct, tag = blob[len(MAGIC) + NONCE_BYTES:-TAG_BYTES], blob[-TAG_BYTES:]
    want = hmac.new(mac_key, MAGIC + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(want, tag):
        raise BundleAuthError("the bundle's tag does not verify (wrong key, or a changed bundle)")
    return _xor_stream(enc_key, nonce, ct)
