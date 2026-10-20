from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from src.agents import buildings as agent_buildings
from src.agents import credentials, operations, renders, service
from src.agents.models import LabellingAgent
from src.agents.schemas import (
    AgentAnnotate,
    AgentBuildingEdit,
    AgentBuildingOut,
    AgentBuildingsCreate,
    AgentBuildingsDelete,
    AgentCredentialCreate,
    AgentCredentialOut,
    AgentOperationRequest,
    AgentOut,
    AgentRegister,
    AgentRenderCreate,
    AgentRenderOut,
    AgentSceneOut,
    AgentSceneOutcome,
    AgentsOverviewOut,
    AgentUpdate,
    NextTasksOut,
    ReleasedTasksOut,
)
from src.annotation.schemas import AnnotationTaskSubmitResponse
from src.auth.dependencies import require_authenticated_user
from src.auth.models import User
from src.buildings import service as buildings_service
from src.buildings.schemas import BuildingProgressOut
from src.campaigns.dependencies import require_campaign_access
from src.campaigns.models import Campaign
from src.database import get_db

bearer = HTTPBearer()  # Using only for adding bearer scheme to Swagger OpenAPI
router = APIRouter(
    tags=["Agents"],
    dependencies=[Depends(bearer), Depends(require_authenticated_user)],
)


@router.post("/campaigns/{campaign_id}/agents", response_model=AgentOut)
def register_agent(
    body: AgentRegister,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> AgentOut:
    service.owner_role(db, campaign, user)

    def register(work: Session) -> AgentOut:
        work_campaign = work.get(Campaign, campaign.id)
        work_user = work.get(User, user.id)
        if work_campaign is None or work_user is None:
            raise HTTPException(status_code=404, detail="Campaign or principal no longer exists")
        agent = service.register_agent(work, work_campaign, work_user, body)
        return service.agent_out(work, agent, campaign.project_id)

    return operations.execute(
        db,
        principal_id=user.id,
        campaign_id=campaign.id,
        target=f"campaign:{campaign.id}:register",
        lock_scope=f"agent-owner:{user.id}",
        request=body,
        operation_id=body.operation_id,
        response_type=TypeAdapter(AgentOut),
        action=register,
    )


def _write[T](
    db: Session,
    user: User,
    agent_id: UUID,
    target: str,
    body: AgentOperationRequest,
    response_type: TypeAdapter[T],
    action: Callable[[Session, LabellingAgent, Campaign], T],
) -> T:
    _, campaign, _ = service.get_agent(db, agent_id, user)

    def run(work: Session) -> T:
        agent, current_campaign, _ = service.get_agent(work, agent_id, user)
        return action(work, agent, current_campaign)

    return operations.execute(
        db,
        principal_id=user.id,
        campaign_id=campaign.id,
        target=f"agent:{agent_id}:{target}",
        lock_scope=f"agent:{agent_id}",
        request=body,
        operation_id=body.operation_id,
        response_type=response_type,
        action=run,
    )


@router.get("/campaigns/{campaign_id}/agents", response_model=AgentsOverviewOut)
def list_agents(
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> AgentsOverviewOut:
    """Open and total tasks and the caller's agents, for sizing a labelling run."""
    return service.agents_overview(db, campaign, user)


@router.post("/campaigns/{campaign_id}/agents/release-tasks", response_model=ReleasedTasksOut)
def release_agent_tasks(
    agent_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> ReleasedTasksOut:
    """Take unfinished tasks off one of the caller's agents, or off all of them."""
    role = service.owner_role(db, campaign, user)
    agents = service.owned_agents(db, campaign.id, user)
    if agent_id is not None:
        agents = [agent for agent in agents if agent.user_id == agent_id]
        if not agents:
            raise HTTPException(status_code=404, detail="Agent not found")
    return ReleasedTasksOut(released=service.release_tasks(db, user, role, agents))


@router.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(
    agent_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AgentOut:
    agent, campaign, _ = service.get_agent(db, agent_id, user)
    return service.agent_out(db, agent, campaign.project_id)


@router.get("/agents/{agent_id}/buildings/progress", response_model=BuildingProgressOut)
def agent_building_progress(
    agent_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> BuildingProgressOut:
    agent, campaign, _ = service.get_agent(db, agent_id, user)
    return buildings_service.progress(db, campaign, agent.user_id)


@router.get("/agents/{agent_id}/tasks/{task_id}/scene", response_model=AgentSceneOut)
def get_agent_scene(
    agent_id: UUID,
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AgentSceneOut:
    agent, campaign, _ = service.get_agent(db, agent_id, user)
    return agent_buildings.scene(db, agent, campaign, task_id)


@router.patch("/agents/{agent_id}", response_model=AgentOut)
def update_agent(
    agent_id: UUID,
    body: AgentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AgentOut:
    agent, campaign, _ = service.get_agent(db, agent_id, user)
    agent.takes_over_work = body.takes_over_work
    db.commit()
    return service.agent_out(db, agent, campaign.project_id)


@router.post("/agents/{agent_id}/credentials", response_model=AgentCredentialOut)
def create_agent_credential(
    agent_id: UUID,
    body: AgentCredentialCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AgentCredentialOut:
    service.get_agent(db, agent_id, user)
    return credentials.mint(db, agent_id, body.expires_in)


@router.delete("/agents/{agent_id}/credentials/{credential_id}", status_code=204)
def revoke_agent_credential(
    agent_id: UUID,
    credential_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> None:
    service.get_agent(db, agent_id, user)
    credentials.revoke(db, agent_id, credential_id)


@router.post("/agents/{agent_id}/tasks/{task_id}/renders", response_model=AgentRenderOut)
def register_agent_render(
    agent_id: UUID,
    task_id: int,
    body: AgentRenderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AgentRenderOut:
    agent, campaign, _ = service.get_agent(db, agent_id, user)
    agent_buildings.scene(db, agent, campaign, task_id)
    return renders.register(db, agent_id, task_id, body)


@router.post("/agents/{agent_id}/next", response_model=NextTasksOut)
def next_agent_tasks(
    agent_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> NextTasksOut:
    return service.next_tasks(db, agent_id, user)


@router.post(
    "/agents/{agent_id}/tasks/{task_id}/annotate", response_model=AnnotationTaskSubmitResponse
)
def annotate_agent_task(
    agent_id: UUID,
    task_id: int,
    body: AgentAnnotate,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AnnotationTaskSubmitResponse:
    return _write(
        db,
        user,
        agent_id,
        f"task:{task_id}:annotate",
        body,
        TypeAdapter(AnnotationTaskSubmitResponse),
        lambda work, agent, campaign: service.submit_annotation(
            work, agent_id, user, task_id, body
        ),
    )


@router.post("/agents/{agent_id}/tasks/{task_id}/buildings", response_model=list[AgentBuildingOut])
def draw_agent_buildings(
    agent_id: UUID,
    task_id: int,
    body: AgentBuildingsCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> list[AgentBuildingOut]:
    """Add buildings to one of the agent's scenes; returns all its buildings there."""
    return _write(
        db,
        user,
        agent_id,
        f"task:{task_id}:draw",
        body,
        TypeAdapter(list[AgentBuildingOut]),
        lambda work, agent, campaign: agent_buildings.draw(work, agent, campaign, task_id, body),
    )


@router.post(
    "/agents/{agent_id}/tasks/{task_id}/buildings/delete", response_model=list[AgentBuildingOut]
)
def delete_agent_buildings(
    agent_id: UUID,
    task_id: int,
    body: AgentBuildingsDelete,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> list[AgentBuildingOut]:
    """Delete some of the agent's buildings in a scene; returns the ones left."""
    return _write(
        db,
        user,
        agent_id,
        f"task:{task_id}:delete",
        body,
        TypeAdapter(list[AgentBuildingOut]),
        lambda work, agent, campaign: agent_buildings.delete(
            work, agent, campaign, task_id, body.building_ids
        ),
    )


@router.patch(
    "/agents/{agent_id}/tasks/{task_id}/buildings/{building_id}",
    response_model=list[AgentBuildingOut],
)
def edit_agent_building(
    agent_id: UUID,
    task_id: int,
    building_id: int,
    body: AgentBuildingEdit,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> list[AgentBuildingOut]:
    return _write(
        db,
        user,
        agent_id,
        f"task:{task_id}:edit:{building_id}",
        body,
        TypeAdapter(list[AgentBuildingOut]),
        lambda work, agent, campaign: agent_buildings.edit(
            work, agent, campaign, task_id, building_id, body
        ),
    )


@router.post(
    "/agents/{agent_id}/tasks/{task_id}/outcome", response_model=AnnotationTaskSubmitResponse
)
def set_agent_scene_outcome(
    agent_id: UUID,
    task_id: int,
    body: AgentSceneOutcome,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> AnnotationTaskSubmitResponse:
    """Finish one of the agent's scenes as Complete or Empty."""
    return _write(
        db,
        user,
        agent_id,
        f"task:{task_id}:outcome",
        body,
        TypeAdapter(AnnotationTaskSubmitResponse),
        lambda work, agent, campaign: agent_buildings.set_outcome(
            work, agent, campaign, task_id, body
        ),
    )


@router.post("/agents/{agent_id}/buildings/hand-in", response_model=BuildingProgressOut)
def hand_in_agent_buildings(
    agent_id: UUID,
    body: AgentOperationRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> BuildingProgressOut:
    """Hand in the agent's finished scenes, as a person does."""
    return _write(
        db,
        user,
        agent_id,
        "hand-in",
        body or AgentOperationRequest(),
        TypeAdapter(BuildingProgressOut),
        lambda work, agent, campaign: agent_buildings.hand_in(work, agent, campaign),
    )
