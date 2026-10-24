"""Verify OpenAI ID tokens with pinned issuer/JWKS and a restricted algorithm set."""

import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ISSUER = "https://auth.openai.com"
JWKS_URL = ISSUER + "/.well-known/jwks.json"


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


async def verify_id_token(token, client_id, client, *, nonce=None):
    if not isinstance(token, str) or len(token) > 32768:
        raise ValueError("Invalid ID token")
    header_part, body_part, signature_part = token.split(".")
    header, claims = json.loads(decode(header_part)), json.loads(decode(body_part))
    if header.get("alg") not in {"RS256", "ES256"} or not isinstance(header.get("kid"), str):
        raise ValueError("Unsupported ID token signature")
    response = await client.get(JWKS_URL)
    response.raise_for_status()
    keys = response.json().get("keys", [])
    key = next((key for key in keys if key.get("kid") == header["kid"] and
                key.get("use", "sig") == "sig" and key.get("alg", header["alg"]) == header["alg"]), None)
    if not key:
        raise ValueError("Unknown ID token signing key")
    signed = (header_part + "." + body_part).encode("ascii")
    signature = decode(signature_part)
    if header["alg"] == "RS256" and key.get("kty") == "RSA":
        public_key = rsa.RSAPublicNumbers(int.from_bytes(decode(key["e"])), int.from_bytes(decode(key["n"]))).public_key()
        public_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    elif header["alg"] == "ES256" and key.get("kty") == "EC" and key.get("crv") == "P-256" and len(signature) == 64:
        public_key = ec.EllipticCurvePublicNumbers(int.from_bytes(decode(key["x"])), int.from_bytes(decode(key["y"])), ec.SECP256R1()).public_key()
        public_key.verify(encode_dss_signature(int.from_bytes(signature[:32]), int.from_bytes(signature[32:])), signed, ec.ECDSA(hashes.SHA256()))
    else:
        raise ValueError("ID token key/algorithm mismatch")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    now = time.time()
    if (claims.get("iss") != ISSUER or client_id not in audiences
            or (len(audiences) > 1 and claims.get("azp") != client_id)
            or not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= now
            or not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > now + 30
            or not isinstance(claims.get("sub"), str) or not claims["sub"]
            or not isinstance(claims.get("nbf", 0), (int, float)) or claims.get("nbf", 0) > now + 30
            or (nonce is not None and claims.get("nonce") != nonce)):
        raise ValueError("Invalid ID token claims")
    return {key: claims[key] for key in ("sub", "email", "name") if isinstance(claims.get(key), str)}
