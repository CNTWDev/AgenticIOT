"""Resource authorization boundary; local credentials are replaced by external grants later."""

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import compare_digest
from typing import Annotated, Literal

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from agenticiot.access.service import LOCAL_CLIENT, LOCAL_ISSUER, MANAGE, READ, resolve


class APIError(HTTPException):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(status, detail)
        self.code = code


@dataclass(frozen=True)
class Principal:
    subject_ref: str
    domain_ref: str
    role: Literal["operator", "viewer"]
    permissions: frozenset[str] = frozenset()
    issuer: str = LOCAL_ISSUER
    client_id: str = LOCAL_CLIENT
    external_domain_ref: str = ""
    expires_at: float | None = None
    turn_ref: str | None = None

    @property
    def domain_id(self):
        return self.domain_ref

    def require(self, permission):
        if permission not in self.permissions:
            raise APIError(403, "forbidden", "Operation is outside granted permissions")


bearer = HTTPBearer(auto_error=False, scheme_name="LocalAPICredential")


def principal_from_token(app, token: str) -> Principal:
    if len(token) > 16384:
        raise APIError(401, "unauthorized", "Credential too large")
    settings = app.state.settings
    identity = None
    for client in settings.api_clients:
        if compare_digest(token.encode(), client.token.get_secret_value().encode()):
            identity = (
                LOCAL_ISSUER,
                LOCAL_CLIENT,
                client.domain_ref,
                client.subject_ref,
                MANAGE if client.role == "operator" else READ,
                None,
                None,
            )
            break
    if identity is None:
        for issuer in settings.trusted_issuers:
            try:
                header = jwt.get_unverified_header(token)
                if header.get("kid") != issuer.key_id:
                    continue
                claims = jwt.decode(
                    token,
                    issuer.public_key,
                    algorithms=["EdDSA"],
                    audience=settings.token_audience,
                    issuer=issuer.issuer,
                    options={
                        "require": [
                            "exp",
                            "iat",
                            "sub",
                            "iss",
                            "aud",
                            "client_id",
                            "domain_ref",
                            "permissions",
                        ]
                    },
                )
                if claims["client_id"] != issuer.client_id:
                    continue
                if not all(
                    isinstance(claims[k], str) and 0 < len(claims[k]) <= 200
                    for k in ["sub", "domain_ref"]
                ):
                    continue
                if not isinstance(claims["permissions"], list) or not all(
                    isinstance(p, str) for p in claims["permissions"]
                ):
                    continue
                if claims["exp"] - claims["iat"] > 3600:
                    continue
                turn = claims.get("turn_ref")
                if turn is not None and (not isinstance(turn, str) or not 0 < len(turn) <= 200):
                    continue
                identity = (
                    issuer.issuer,
                    issuer.client_id,
                    claims["domain_ref"],
                    claims["sub"],
                    claims["permissions"],
                    claims["exp"],
                    turn,
                )
                break
            except (jwt.PyJWTError, ValueError, TypeError):
                continue
    if identity is not None:
        issuer, client_id, external, subject, requested, expiry, turn = identity
        if app.state.engine is None:
            raise APIError(503, "database_not_ready", "Database is not ready")
        with Session(app.state.engine) as session:
            domain, permissions = resolve(session, issuer, client_id, external, subject, requested)
        return Principal(
            subject,
            domain,
            "operator" if "domain:manage" in permissions else "viewer",
            permissions,
            issuer,
            client_id,
            external,
            expiry,
            turn,
        )
    error = APIError(401, "unauthorized", "A valid API credential is required")
    error.headers = {"WWW-Authenticate": "Bearer"}
    raise error


def authenticate(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    return principal_from_token(request.app, credentials.credentials if credentials else "")


def revalidate(app, principal):
    if principal.expires_at is not None and principal.expires_at <= datetime.now(UTC).timestamp():
        raise APIError(401, "token_expired", "Credential expired")
    with Session(app.state.engine) as session:
        return resolve(
            session,
            principal.issuer,
            principal.client_id,
            principal.external_domain_ref,
            principal.subject_ref,
            principal.permissions,
        )[1]


Authenticated = Annotated[Principal, Depends(authenticate)]


def require_operator(principal: Authenticated) -> Principal:
    principal.require("domain:manage")
    return principal


def require_action(principal: Authenticated) -> Principal:
    principal.require("device:act")
    return principal


Operator = Annotated[Principal, Depends(require_operator)]
Actuator = Annotated[Principal, Depends(require_action)]


@dataclass(frozen=True)
class EdgePrincipal:
    edge_ref: str
    domain_ref: str

    @property
    def subject_ref(self):
        return self.edge_ref


edge_bearer = HTTPBearer(auto_error=False, scheme_name="LocalEdgeCredential")


def authenticate_edge(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(edge_bearer)],
) -> EdgePrincipal:
    for client in request.app.state.settings.edge_clients:
        if credentials is not None and compare_digest(
            credentials.credentials.encode(), client.token.get_secret_value().encode()
        ):
            from sqlalchemy import select

            from agenticiot.access.models import Domain, DomainAlias

            if request.app.state.engine is None:
                raise APIError(503, "database_not_ready", "Database is not ready")
            with Session(request.app.state.engine) as session:
                alias = session.scalar(
                    select(DomainAlias).where(
                        DomainAlias.issuer == LOCAL_ISSUER,
                        DomainAlias.client_id == LOCAL_CLIENT,
                        DomainAlias.external_ref == client.domain_ref,
                    )
                )
                if alias is None or not session.get(Domain, alias.domain_id).enabled:
                    raise APIError(403, "domain_not_linked", "Node domain is not active")
                return EdgePrincipal(client.edge_ref, alias.domain_id)
    error = APIError(401, "unauthorized", "A valid Edge credential is required")
    error.headers = {"WWW-Authenticate": "Bearer"}
    raise error


EdgeAuthenticated = Annotated[EdgePrincipal, Depends(authenticate_edge)]
