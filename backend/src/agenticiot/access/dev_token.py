"""Explicit local entry simulator. Never mounted as a platform HTTP endpoint."""

import argparse
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "issue"])
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--issuer", default="urn:agenticiot:entry-demo")
    parser.add_argument("--client-id", default="demo-app")
    parser.add_argument("--key-id", default="demo-1")
    parser.add_argument("--subject", default="owner:demo")
    parser.add_argument("--domain", default="home:demo")
    parser.add_argument(
        "--permissions", default="device:read,device:act,events:read,service:invoke,domain:manage"
    )
    parser.add_argument("--turn-ref")
    args = parser.parse_args()
    if args.command == "generate":
        key = Ed25519PrivateKey.generate()
        # Exclusive creation; never replace a signing identity accidentally.
        fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        print(
            key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        )
        return
    if args.key.stat().st_mode & 0o077:
        parser.error("Private key must be accessible only to its owner (chmod 600)")
    key = load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("An Ed25519 private key is required")
    now = datetime.now(UTC)
    claims = {
        "iss": args.issuer,
        "aud": "agenticiot-platform",
        "client_id": args.client_id,
        "sub": args.subject,
        "domain_ref": args.domain,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "permissions": args.permissions.split(","),
    }
    if args.turn_ref:
        claims["turn_ref"] = args.turn_ref
    print(jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": args.key_id}))


if __name__ == "__main__":
    main()
