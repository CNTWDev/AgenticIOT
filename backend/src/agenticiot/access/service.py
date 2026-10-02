from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenticiot.access.models import Domain, DomainAlias, DomainGrant

READ = frozenset({"device:read", "events:read"})
MANAGE = READ | {"device:act", "domain:manage", "service:invoke"}
LOCAL_ISSUER = "urn:agenticiot:development"
LOCAL_CLIENT = "configured-client"


def resolve(session, issuer, client_id, external_ref, subject, requested):
    from agenticiot.security import APIError

    alias = session.scalar(
        select(DomainAlias).where(
            DomainAlias.issuer == issuer,
            DomainAlias.client_id == client_id,
            DomainAlias.external_ref == external_ref,
        )
    )
    if alias is None:
        raise APIError(403, "domain_not_linked", "Entry is not linked to a resource domain")
    domain = session.get(Domain, alias.domain_id)
    grant = session.scalar(
        select(DomainGrant).where(
            DomainGrant.alias_id == alias.id, DomainGrant.subject_ref == subject
        )
    )
    if (
        not domain.enabled
        or grant is None
        or not grant.enabled
        or (grant.expires_at is not None and grant.expires_at <= datetime.now(UTC))
    ):
        raise APIError(403, "grant_denied", "No active resource grant")
    return domain.id, frozenset(requested) & frozenset(grant.permissions)


def provision(session, issuer, client_id, external_ref, subject, permissions):
    """Explicit operator bootstrap only; never called from authentication or HTTP startup."""
    alias = session.scalar(
        select(DomainAlias).where(
            DomainAlias.issuer == issuer,
            DomainAlias.client_id == client_id,
            DomainAlias.external_ref == external_ref,
        )
    )
    if alias is None:
        domain = Domain(id=uuid4().hex, title=external_ref[:160])
        session.add(domain)
        session.flush()
        alias = DomainAlias(
            id=uuid4().hex,
            issuer=issuer,
            client_id=client_id,
            external_ref=external_ref,
            domain_id=domain.id,
        )
        session.add(alias)
        session.flush()
    grant = session.scalar(
        select(DomainGrant).where(
            DomainGrant.alias_id == alias.id, DomainGrant.subject_ref == subject
        )
    )
    if grant is None:
        session.add(
            DomainGrant(
                id=uuid4().hex,
                alias_id=alias.id,
                subject_ref=subject,
                permissions=sorted(permissions),
            )
        )
    # Existing revoked/restricted grants are never silently broadened by a rerun.
    session.flush()
    return alias.domain_id


def bootstrap_configured(engine, settings):
    with Session(engine) as session, session.begin():
        for client in settings.api_clients:
            provision(
                session,
                LOCAL_ISSUER,
                LOCAL_CLIENT,
                client.domain_ref,
                client.subject_ref,
                MANAGE if client.role == "operator" else READ,
            )


def main():
    import argparse

    from agenticiot.config import Settings
    from agenticiot.database import build_engine

    parser = argparse.ArgumentParser(description="Explicit platform-controlled grant bootstrap")
    parser.add_argument("--issuer")
    parser.add_argument("--client-id")
    parser.add_argument("--external-domain")
    parser.add_argument("--subject")
    parser.add_argument("--permissions", default=",".join(sorted(MANAGE)))
    args = parser.parse_args()
    identity = [args.issuer, args.client_id, args.external_domain, args.subject]
    if any(identity) and not all(identity):
        parser.error("Supply issuer, client-id, external-domain and subject together")
    permissions = set(args.permissions.split(","))
    if not permissions <= MANAGE:
        parser.error("Unknown permission")
    settings = Settings()
    engine = build_engine(settings)
    try:
        if all(identity):
            if not any(
                i.issuer == args.issuer and i.client_id == args.client_id
                for i in settings.trusted_issuers
            ):
                parser.error("Configure the trusted issuer/client public key first")
            with Session(engine) as session, session.begin():
                domain_id = provision(session, *identity, permissions)
            print("Resource domain:", domain_id)
        else:
            bootstrap_configured(engine, settings)
        print("Configured entry aliases and initial grants provisioned; existing grants preserved.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
