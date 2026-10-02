# ADR 0003: Foundation workspace and operational checks

Status: Accepted

## Decision

Use a root uv-managed Python package with `backend/src` layout and npm workspaces with `apps/admin`. Python 3.13, FastAPI and synchronous SQLAlchemy/psycopg provide the initial API foundation; blocking database work runs in FastAPI's synchronous endpoint thread pool. React, TypeScript and Vite provide the Admin Console.

PostgreSQL and Alembic are mandatory for database readiness. A baseline revision deliberately creates no domain tables. Runtime startup never migrates the database automatically.

Expose separate liveness and readiness probes. Readiness requires a reachable database with the expected migration revision, uses bounded connection/query waits and excludes credentials from responses. Liveness remains independent of database availability.

Keep the planned Capability API and actual FastAPI schema separate. Validate both against OpenAPI and commit generated reference material; CI rejects stale artifacts. There are no fake successful responses for unimplemented business operations.

## Consequences

- The initial Admin Console can diagnose the foundation before device APIs exist.
- Domain tables and authentication enforcement arrive with actual resource endpoints.
- Local development works either with Compose or a separately managed PostgreSQL instance.
- Backend API and readiness stay testable without a database; migration integration tests require an explicit disposable database.
- New migrations must update the readiness revision alongside the code expecting that schema.
- Vite proxies API requests locally; Nginx handles the same path contract in Compose.
- The CI architecture gate checks for accompanying design documentation; it complements reviewer judgment rather than interpreting architecture automatically.
