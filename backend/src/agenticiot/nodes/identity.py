import hashlib
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from agenticiot.access.models import Domain
from agenticiot.nodes.models import NodeCredential
from agenticiot.runtime.models import EdgeNode
from agenticiot.security import APIError, EdgePrincipal


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def provision_node(session, domain_id, edge_ref, title):
    node = session.scalar(
        select(EdgeNode)
        .where(EdgeNode.domain_ref == domain_id, EdgeNode.edge_ref == edge_ref)
        .with_for_update()
    )
    if node is not None and session.get(NodeCredential, node.id) is not None:
        raise APIError(409, "resource_conflict", "Node is already managed; use credential rotation")
    if node is None:
        node = EdgeNode(
            id=uuid4().hex,
            domain_ref=domain_id,
            edge_ref=edge_ref,
            title=title,
            version="0.1.0",
            adapters=["virtual-light-v1"],
            last_seen_at=datetime(1970, 1, 1, tzinfo=UTC),
            last_sequence=0,
        )
        session.add(node)
    else:
        # Explicit domain-admin adoption preserves pre-WSS bindings and journal identity.
        node.title = title
    session.flush()
    token = secrets.token_urlsafe(48)
    session.add(NodeCredential(node_id=node.id, token_hash=token_hash(token)))
    session.flush()
    return node, token


def authenticate_node(app, token):
    if app.state.engine is None:
        raise APIError(503, "database_not_ready", "Database is not ready")
    with Session(app.state.engine) as session:
        credential = session.scalar(
            select(NodeCredential).where(
                NodeCredential.token_hash == token_hash(token), NodeCredential.enabled.is_(True)
            )
        )
        if credential is None:
            raise APIError(401, "unauthorized", "Invalid node credential")
        node = session.get(EdgeNode, credential.node_id)
        if not session.get(Domain, node.domain_ref).enabled:
            raise APIError(403, "domain_disabled", "Resource domain is disabled")
        return EdgePrincipal(node.edge_ref, node.domain_ref)
