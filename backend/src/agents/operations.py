"""Atomic agent operations over existing services that commit their own work."""

import hashlib
import json
from collections.abc import Callable
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.agents.models import AgentOperation


def execute[T](
    db: Session,
    *,
    principal_id: UUID,
    campaign_id: int,
    target: str,
    lock_scope: str,
    request: BaseModel,
    operation_id: UUID | None,
    response_type: TypeAdapter[T],
    action: Callable[[Session], T],
) -> T:
    payload = json.dumps(
        request.model_dump(mode="json", exclude={"operation_id"}),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    lock = int.from_bytes(hashlib.sha256(lock_scope.encode()).digest()[:8], signed=True)
    try:
        db.execute(select(func.pg_advisory_xact_lock(lock)))
        key = (principal_id, target, operation_id)
        previous = db.get(AgentOperation, key) if operation_id is not None else None
        if previous is not None:
            if previous.payload_hash != digest:
                raise HTTPException(status_code=409, detail="Operation ID payload mismatch")
            result = response_type.validate_python(previous.response)
        else:
            # Service commits release only savepoints, never the outer transaction.
            # A later assignment/response failure rolls back every earlier effect.
            with Session(
                bind=db.connection(), join_transaction_mode="create_savepoint", autoflush=False
            ) as work:
                work.info.update(db.info)
                result = action(work)
                work.commit()
            if operation_id is not None:
                db.add(
                    AgentOperation(
                        principal_id=principal_id,
                        campaign_id=campaign_id,
                        target=target,
                        operation_id=operation_id,
                        payload_hash=digest,
                        response=response_type.dump_python(result, mode="json"),
                    )
                )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
