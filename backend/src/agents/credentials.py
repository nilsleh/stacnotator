"""Deny-by-default execution capabilities, separate from an owner's sign-in."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.models import AgentCredential, LabellingAgent
from src.agents.schemas import AgentCredentialOut
from src.auth.models import User

PREFIX = "stac-agent-"
EXECUTION_ROUTES = frozenset(
    {
        "me",
        "get_tiler_token",
        "get_campaign_with_imagery_windows",
        "get_agent",
        "agent_building_progress",
        "get_agent_scene",
        "next_agent_tasks",
        "annotate_agent_task",
        "draw_agent_buildings",
        "delete_agent_buildings",
        "set_agent_scene_outcome",
        "hand_in_agent_buildings",
        "register_agent_render",
        "edit_agent_building",
        "search_planet_scenes",
        "mint_planet_scene_layers",
    }
)


def execution_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token.startswith(PREFIX) else None


def mint(db: Session, agent_id: UUID, ttl: int) -> AgentCredentialOut:
    token = PREFIX + secrets.token_urlsafe(32)
    record = AgentCredential(
        id=uuid4(),
        agent_id=agent_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
    )
    db.add(record)
    db.commit()
    return AgentCredentialOut(
        credential_id=record.id,
        token=token,
        expires_at=record.expires_at,
    )


def authenticate(db: Session, request: Request, token: str) -> User:
    record = db.scalar(
        select(AgentCredential).where(
            AgentCredential.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if record is None or record.revoked_at is not None or record.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Execution credential expired or revoked")
    agent = db.get(LabellingAgent, record.agent_id)
    if agent is None:
        raise HTTPException(status_code=401, detail="Execution agent no longer exists")
    route = request.scope.get("route")
    params = request.path_params
    if (
        getattr(route, "name", None) not in EXECUTION_ROUTES
        or ("agent_id" in params and str(params["agent_id"]) != str(agent.user_id))
        or ("campaign_id" in params and int(params["campaign_id"]) != agent.campaign_id)
    ):
        raise HTTPException(status_code=403, detail="Outside execution credential scope")
    db.info["execution_agent_id"] = agent.user_id
    db.info["execution_campaign_id"] = agent.campaign_id
    db.info["execution_expires_at"] = record.expires_at
    return agent.user


def revoke(db: Session, agent_id: UUID, credential_id: UUID) -> None:
    record = db.get(AgentCredential, credential_id)
    if record is None or record.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Execution credential not found")
    record.revoked_at = datetime.now(UTC)
    db.commit()
