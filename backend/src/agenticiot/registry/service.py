import base64
import json
import re
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from agenticiot.registry.models import Device, DeviceModel, RegistryAudit
from agenticiot.registry.schemas import DeviceCreate, DevicePatch, ModelCreate
from agenticiot.security import APIError, Principal


class RegistryService:
    def __init__(self, session: Session, principal: Principal, trace_id: str):
        self.session = session
        self.principal = principal
        self.domain = principal.domain_ref
        self.trace_id = trace_id

    def audit(self, operation: str, resource_id: str, *, actor: str | None = None):
        self.session.add(
            RegistryAudit(
                id=uuid4().hex,
                domain_ref=self.domain,
                subject_ref=actor or self.principal.subject_ref,
                operation=operation,
                resource_id=resource_id,
                trace_id=self.trace_id,
            )
        )

    def publish_model(self, data: ModelCreate) -> DeviceModel:
        model = DeviceModel(
            id=uuid4().hex, domain_ref=self.domain, **data.model_dump(by_alias=True)
        )
        self.session.add(model)
        self.audit("model.published", model.id)
        self.session.commit()
        return model

    def get(self, kind, resource_id):
        resource = self.session.scalar(
            select(kind).where(kind.id == resource_id, kind.domain_ref == self.domain)
        )
        if resource is None:
            raise APIError(404, "resource_not_found", "Resource not found")
        return resource

    def register_device(self, data: DeviceCreate) -> Device:
        model = self.get(DeviceModel, data.model_id)
        device = Device(
            id=uuid4().hex, domain_ref=self.domain, model_version=model.version, **data.model_dump()
        )
        self.session.add(device)
        self.audit("device.registered", device.id)
        self.session.commit()
        return device

    def edit_device(self, device_id: str, data: DevicePatch, if_match: str | None) -> Device:
        if if_match is None:
            raise APIError(428, "precondition_required", "If-Match is required")
        device = self.get(Device, device_id)
        if if_match != f'"{device.revision}"':
            raise APIError(412, "revision_conflict", "Device was updated; reload before editing")
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(device, key, value)
        device.revision += 1
        self.audit("device.updated", device.id)
        self.session.commit()
        return device

    def page(self, kind, limit: int, cursor: str | None, *, space_ref=None, capability=None):
        scope = [kind.__tablename__, self.domain, space_ref, capability]
        statement = select(kind).where(kind.domain_ref == self.domain)
        if kind is Device:
            if space_ref is not None:
                statement = statement.where(Device.space_ref == space_ref)
            if capability is not None:
                statement = statement.join(DeviceModel, DeviceModel.id == Device.model_id).where(
                    or_(
                        DeviceModel.properties.has_key(capability),
                        DeviceModel.actions.has_key(capability),
                        DeviceModel.events.has_key(capability),
                    )
                )
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if decoded["scope"] != scope or not re.fullmatch(r"[a-f0-9]{32}", decoded["after"]):
                    raise ValueError
                after = decoded["after"]
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise APIError(400, "invalid_cursor", "Cursor does not match this query") from None
            statement = statement.where(kind.id > after)
        rows = list(self.session.scalars(statement.order_by(kind.id).limit(limit + 1)))
        next_cursor = None
        if len(rows) > limit:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps({"scope": scope, "after": rows[limit - 1].id}).encode()
            ).decode()
        return rows[:limit], next_cursor
