"""Check a Verdict's signature yourself, with no call to Datagoat. Standard library only.

    from datagoat.verify import verify_offline, fetch_keys
    keys = fetch_keys()                                  # once; cache it
    verify_offline(v["verdict"], v["signature"], keys)   # "valid" | "invalid_signature" | "expired" | "unknown_key"

A Verdict is signed with Ed25519 as a detached JWS (`b64: false`): the signed bytes are the
protected header, a dot, and the Verdict as compact JSON with its keys sorted. The public keys are
at https://api.datagoat.io/.well-known/jwks.json. The statuses are the same as `Client.verify`.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import urllib.request
from typing import Any, Dict, Mapping, Optional, Sequence

JWKS_URL = "https://api.datagoat.io/.well-known/jwks.json"

# --- Ed25519 verification (RFC 8032, section 5.1.7), pure Python. It is slow (about 10 ms a
# Verdict) but has no dependency, which keeps the SDK standard-library only.
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
_G_Y = 4 * pow(5, _P - 2, _P) % _P


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = 2 * t1 * t2 * _D % _P
    D = 2 * z1 * z2 % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _eq(a, b) -> bool:
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(b: bytes):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_G = (_recover_x(_G_Y, 0), _G_Y, 1, _recover_x(_G_Y, 0) * _G_Y % _P)


def _ed25519_verify(public: bytes, msg: bytes, sig: bytes) -> bool:
    if len(public) != 32 or len(sig) != 64:
        return False
    A = _decompress(public)
    R = _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + public + msg).digest(), "little") % _L
    return _eq(_mul(s, _G), _add(R, _mul(h, A)))


# --- The Verdict envelope.

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def payloads(verdict: Mapping[str, Any]) -> Sequence[bytes]:
    """The signed bytes: compact JSON, keys sorted. Both escapings of non-ASCII text are tried."""
    return [json.dumps(verdict, sort_keys=True, separators=(",", ":"), ensure_ascii=a).encode("utf-8")
            for a in (True, False)]


def fetch_keys(url: str = JWKS_URL, timeout: float = 10.0) -> Dict[str, Any]:
    """The published key set. Fetch once and keep it; keys rotate by quarter (`kid`)."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def verify_offline(verdict: Mapping[str, Any], signature: Optional[Mapping[str, Any]],
                   keys: Mapping[str, Any], *, now: Optional[_dt.datetime] = None) -> str:
    """valid | invalid_signature | expired | unknown_key, with no network call.

    Pass the Verdict and signature exactly as Datagoat returned them. Never act on anything but valid.
    """
    if not signature or "protected" not in signature or "signature" not in signature:
        return "invalid_signature"
    try:
        header = json.loads(_b64d(signature["protected"]))
    except (ValueError, TypeError):
        return "invalid_signature"
    if header.get("alg") != "EdDSA" or header.get("b64") is not False:
        return "invalid_signature"
    kid = header.get("kid") or signature.get("kid")
    jwk = next((k for k in keys.get("keys", []) if k.get("kid") == kid and k.get("crv") == "Ed25519"), None)
    if jwk is None:
        return "unknown_key"
    public, sig = _b64d(jwk["x"]), _b64d(signature["signature"])
    signed_prefix = signature["protected"].encode("ascii") + b"."
    if not any(_ed25519_verify(public, signed_prefix + p, sig) for p in payloads(verdict)):
        return "invalid_signature"
    expires = verdict.get("expires_at")
    if expires:
        at = _dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        if (now or _dt.datetime.now(_dt.timezone.utc)) >= at:
            return "expired"
    return "valid"
