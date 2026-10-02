"""Validate deployment identity configuration without printing secrets or touching the DB."""

import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from agenticiot.config import Settings


def main():
    try:
        settings = Settings()
        if not settings.api_clients and not settings.trusted_issuers:
            raise ValueError("No trusted identity provider configured")
        for issuer in settings.trusted_issuers:
            if not isinstance(load_pem_public_key(issuer.public_key.encode()), Ed25519PublicKey):
                raise ValueError("Expected Ed25519 public key")
    except Exception:
        print(
            "Invalid deployment configuration: check identity and secret settings.", file=sys.stderr
        )
        raise SystemExit(1) from None
    print("Deployment configuration validated; database grants are checked separately.")


if __name__ == "__main__":
    main()
