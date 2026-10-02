from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from agenticiot.access.models import Domain, DomainAlias, DomainGrant
from agenticiot.nodes.identity import provision_node
from agenticiot.nodes.models import NodeCredential
from agenticiot.registry.schemas import ResourceID, SchemaModel
from agenticiot.registry.service import RegistryService
from agenticiot.runtime.models import EdgeNode
from agenticiot.security import APIError, Authenticated, Operator

router = APIRouter(prefix="/v1/management", tags=["Access and activation"])


def database(request: Request):
    if request.app.state.engine is None:
        raise APIError(503, "database_not_ready", "Database is not ready")
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


Database = Annotated[Session, Depends(database)]


class NodeCreate(SchemaModel):
    edge_ref: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=160)


class Enabled(SchemaModel):
    enabled: bool


class DomainView(SchemaModel):
    id: ResourceID
    title: str
    permissions: list[str]


class NodeProvisioned(SchemaModel):
    id: ResourceID
    domain_id: ResourceID
    edge_ref: str
    token: str
    credential_delivery: str
    online: bool


class NodeView(SchemaModel):
    id: ResourceID
    edge_ref: str
    title: str
    enabled: bool
    online: bool
    last_seen_at: datetime
    pending_uploads: int | None = None
    quarantined_uploads: int | None = None


class NodeList(SchemaModel):
    items: list[NodeView]


class ActivationView(SchemaModel):
    id: ResourceID
    enabled: bool


class RotatedCredential(SchemaModel):
    id: ResourceID
    token: str
    rotated_at: datetime


class GrantView(SchemaModel):
    id: ResourceID
    issuer: str
    client_id: str
    subject_ref: str
    permissions: list[str]
    enabled: bool
    expires_at: datetime | None


class GrantList(SchemaModel):
    items: list[GrantView]


@router.get("/domain", operation_id="getResourceDomain", response_model=DomainView)
def domain(principal: Authenticated, session: Database):
    row = session.get(Domain, principal.domain_id)
    return {"id": row.id, "title": row.title, "permissions": sorted(principal.permissions)}


@router.get("/grants", operation_id="listDomainGrants", response_model=GrantList)
def grants(principal: Operator, session: Database):
    rows = session.execute(
        select(DomainGrant, DomainAlias)
        .join(DomainAlias)
        .where(DomainAlias.domain_id == principal.domain_id)
    ).all()
    return {
        "items": [
            {
                "id": g.id,
                "issuer": a.issuer,
                "client_id": a.client_id,
                "subject_ref": g.subject_ref,
                "permissions": g.permissions,
                "enabled": g.enabled,
                "expires_at": g.expires_at,
            }
            for g, a in rows
        ]
    }


@router.post(
    "/nodes", status_code=201, operation_id="provisionNode", response_model=NodeProvisioned
)
def create_node(data: NodeCreate, request: Request, principal: Operator, session: Database):
    node, token = provision_node(session, principal.domain_id, data.edge_ref, data.title)
    RegistryService(session, principal, request.state.trace_id).audit("node.provisioned", node.id)
    session.commit()
    return {
        "id": node.id,
        "domain_id": principal.domain_id,
        "edge_ref": node.edge_ref,
        "token": token,
        "credential_delivery": "one_time",
        "online": False,
    }


@router.post(
    "/grants/{grant_id}/revoke", operation_id="revokeDomainGrant", response_model=ActivationView
)
def revoke_grant(grant_id: str, request: Request, principal: Operator, session: Database):
    session.scalar(select(Domain).where(Domain.id == principal.domain_id).with_for_update())
    grant = session.scalar(
        select(DomainGrant)
        .join(DomainAlias)
        .where(DomainGrant.id == grant_id, DomainAlias.domain_id == principal.domain_id)
        .with_for_update(of=DomainGrant)
    )
    if grant is None:
        raise APIError(404, "resource_not_found", "Grant not found")
    if grant.enabled and "domain:manage" in grant.permissions:
        others = session.scalars(
            select(DomainGrant)
            .join(DomainAlias)
            .where(
                DomainAlias.domain_id == principal.domain_id,
                DomainGrant.id != grant.id,
                DomainGrant.enabled.is_(True),
            )
        ).all()
        if not any(
            "domain:manage" in g.permissions
            and (g.expires_at is None or g.expires_at > datetime.now(UTC))
            for g in others
        ):
            raise APIError(
                409,
                "last_domain_manager",
                "Grant another domain manager before revoking this grant",
            )
    grant.enabled = False
    RegistryService(session, principal, request.state.trace_id).audit("grant.revoked", grant.id)
    session.commit()
    return {"id": grant.id, "enabled": False}


@router.get("/nodes", operation_id="listManagedNodes", response_model=NodeList)
def nodes(request: Request, principal: Authenticated, session: Database):
    principal.require("device:read")
    rows = session.execute(
        select(EdgeNode, NodeCredential)
        .join(NodeCredential)
        .where(EdgeNode.domain_ref == principal.domain_id)
        .order_by(EdgeNode.id)
        .limit(200)
    ).all()
    return {
        "items": [
            {
                "id": n.id,
                "edge_ref": n.edge_ref,
                "title": n.title,
                "enabled": c.enabled,
                "online": bool(
                    (conn := request.app.state.node_hub.connections.get(n.id)) and not conn.closed
                ),
                "last_seen_at": n.last_seen_at,
                "pending_uploads": conn.journal_stats.get("pending")
                if conn and not conn.closed
                else None,
                "quarantined_uploads": conn.journal_stats.get("quarantined")
                if conn and not conn.closed
                else None,
            }
            for n, c in rows
        ]
    }


@router.patch(
    "/nodes/{node_id}/activation", operation_id="setNodeActivation", response_model=ActivationView
)
def activate_node(
    node_id: str, data: Enabled, request: Request, principal: Operator, session: Database
):
    node = session.scalar(
        select(EdgeNode)
        .where(EdgeNode.id == node_id, EdgeNode.domain_ref == principal.domain_id)
        .with_for_update()
    )
    credential = session.get(NodeCredential, node_id) if node else None
    if credential is None:
        raise APIError(404, "resource_not_found", "Managed node not found")
    credential.enabled = data.enabled
    RegistryService(session, principal, request.state.trace_id).audit("node.activation", node_id)
    session.commit()
    return {"id": node_id, "enabled": data.enabled}


@router.post(
    "/nodes/{node_id}/rotate-credential",
    operation_id="rotateNodeCredential",
    response_model=RotatedCredential,
)
def rotate_node(node_id: str, request: Request, principal: Operator, session: Database):
    import secrets

    from agenticiot.nodes.identity import token_hash

    node = session.scalar(
        select(EdgeNode)
        .where(EdgeNode.id == node_id, EdgeNode.domain_ref == principal.domain_id)
        .with_for_update()
    )
    credential = session.get(NodeCredential, node_id) if node else None
    if credential is None:
        raise APIError(404, "resource_not_found", "Managed node not found")
    token = secrets.token_urlsafe(48)
    credential.token_hash = token_hash(token)
    RegistryService(session, principal, request.state.trace_id).audit(
        "node.credential_rotated", node_id
    )
    session.commit()
    return {"id": node_id, "token": token, "rotated_at": datetime.now(UTC)}
