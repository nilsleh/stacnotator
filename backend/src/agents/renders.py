"""Short-lived immutable render metadata; image bytes remain with the client."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.agents.models import AgentRender
from src.agents.schemas import AgentRenderCreate, AgentRenderOut

RENDER_TTL = timedelta(hours=1)


def register(db: Session, agent_id: UUID, task_id: int, body: AgentRenderCreate) -> AgentRenderOut:
    task = body.metadata.get("task")
    if not isinstance(task, dict) or task.get("task_id") != task_id:
        raise HTTPException(status_code=400, detail="Render metadata task mismatch")
    now = datetime.now(UTC)
    db.execute(delete(AgentRender).where(AgentRender.expires_at <= now))
    record = AgentRender(
        id=uuid4(),
        agent_id=agent_id,
        task_id=task_id,
        image_sha256=body.image_sha256,
        image_metadata=body.metadata,
        expires_at=now + RENDER_TTL,
    )
    db.add(record)
    db.commit()
    return AgentRenderOut(render_id=record.id, expires_at=record.expires_at)


def validate(
    db: Session, render_id: UUID, agent_id: UUID, task_id: int, *, require_ready: bool = False
) -> None:
    record = db.get(AgentRender, render_id)
    if (
        record is None
        or record.agent_id != agent_id
        or record.task_id != task_id
        or record.expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(status_code=409, detail="Unknown, expired or mismatched render ID")
    if require_ready and (
        record.image_metadata.get("status") != "ready"
        or not any(
            isinstance(cell, dict)
            and cell.get("kind") == "imagery"
            and cell.get("status") == "ready"
            for cell in record.image_metadata.get("cells", [])
        )
    ):
        raise HTTPException(
            status_code=409, detail="Scene outcome requires successfully rendered imagery"
        )
