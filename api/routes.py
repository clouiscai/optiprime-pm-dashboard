import csv
import io
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session, joinedload

from database.session import get_db
from models.entities import Blocker, BOMItem, BOMVersion, BudgetLog, Project, Sponsor, Task, TaskAuditLog, Team, User
from models.schemas import (
    BlockerCreate,
    BlockerRead,
    BlockerUpdate,
    BOMItemCreate,
    BOMItemRead,
    BOMItemUpdate,
    BOMVersionRead,
    BudgetLogCreate,
    BudgetLogRead,
    BudgetLogUpdate,
    DashboardRead,
    LoginRequest,
    LoginResponse,
    ProjectCreate,
    ProjectRead,
    SponsorCreate,
    SponsorRead,
    SponsorUpdate,
    TeamCreate,
    TeamRead,
    TeamUpdate,
    TaskAuditRead,
    TaskCreate,
    TaskRead,
    TaskUpdate,
    TokenCheck,
    UserCreate,
    UserRead,
    UserUpdate,
)
from services.auth import account_for_credentials, require_token, require_websocket_token, require_write_token, role_for_token
from services.calculations import project_dashboard, task_with_open_blockers
from services.realtime import manager


router = APIRouter()
Protected = Annotated[str, Depends(require_token)]
Writable = Annotated[str, Depends(require_write_token)]


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def normalized_team_id(team_id: int | None = Query(default=None)) -> int | None:
    return team_id


def write_csv(rows: list[dict], fieldnames: list[str], filename: str) -> Response:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def normalize_bom_item_versions(db: Session, item: BOMItem) -> bool:
    versions = (
        db.query(BOMVersion)
        .filter(BOMVersion.bom_item_id == item.id)
        .order_by(BOMVersion.changed_at.asc(), BOMVersion.id.asc())
        .all()
    )
    changed = False
    for number, version in enumerate(versions, start=1):
        if version.version != number:
            version.version = number
            changed = True
    max_valid_version = len(versions) + 1
    if item.version < 1 or item.version > max_valid_version:
        item.version = max_valid_version
        item.last_updated = datetime.utcnow()
        changed = True
    return changed


def validate_parent_task(db: Session, task: Task, parent_task_id: int | None):
    if not parent_task_id:
        return
    if task.id and parent_task_id == task.id:
        raise HTTPException(400, "A task cannot be its own WBS parent")
    parent = db.get(Task, parent_task_id)
    if not parent or parent.project_id != task.project_id:
        raise HTTPException(400, "WBS parent must be in the same project")
    if parent.team_id != task.team_id:
        raise HTTPException(400, "WBS parent must be in the same team")
    seen = {task.id} if task.id else set()
    cursor = parent
    while cursor.parent_task_id:
        if cursor.parent_task_id in seen:
            raise HTTPException(400, "WBS parent would create a cycle")
        seen.add(cursor.parent_task_id)
        cursor = db.get(Task, cursor.parent_task_id)
        if not cursor:
            break


@router.get("/auth/verify", response_model=TokenCheck)
def verify_token(token: Protected):
    return TokenCheck(ok=True, role=role_for_token(token) or "viewer")


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    account = account_for_credentials(payload.username, payload.password)
    if not account:
        raise HTTPException(401, "Invalid username or password")
    return LoginResponse(token=account["token"], user=account["user"], role=account["role"])


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(_: Protected, db: Session = Depends(get_db)):
    return db.query(Project).order_by(Project.id).all()


@router.post("/projects", response_model=ProjectRead)
async def create_project(payload: ProjectCreate, _: Writable, db: Session = Depends(get_db)):
    project = Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    await manager.broadcast("project.updated", {"project_id": project.id})
    return project


@router.get("/projects/{project_id}/teams", response_model=list[TeamRead])
def list_teams(project_id: int, _: Protected, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return db.query(Team).filter(Team.project_id == project_id).order_by(Team.code).all()


@router.post("/teams", response_model=TeamRead)
async def create_team(payload: TeamCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    team = Team(**payload.model_dump())
    db.add(team)
    db.commit()
    db.refresh(team)
    await manager.broadcast("team.updated", {"project_id": team.project_id, "team_id": team.id})
    return team


@router.patch("/teams/{team_id}", response_model=TeamRead)
async def update_team(team_id: int, payload: TeamUpdate, _: Writable, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(team, field, value)
    db.commit()
    db.refresh(team)
    await manager.broadcast("team.updated", {"project_id": team.project_id, "team_id": team.id})
    return team


@router.get("/projects/{project_id}/dashboard", response_model=DashboardRead)
def get_dashboard(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    return project_dashboard(db, project, team_id)


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRead])
def list_tasks(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    query = (
        db.query(Task)
        .options(joinedload(Task.blockers), joinedload(Task.children))
        .filter(Task.project_id == project_id)
    )
    if team_id:
        query = query.filter(Task.team_id == team_id)
    tasks = query.order_by(Task.due_date.is_(None), Task.due_date, Task.id).all()
    return [task_with_open_blockers(task) for task in tasks]


@router.post("/tasks", response_model=TaskRead)
async def create_task(payload: TaskCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    task = Task(**payload.model_dump())
    validate_parent_task(db, task, task.parent_task_id)
    db.add(task)
    db.commit()
    db.refresh(task)
    await manager.broadcast("task.created", {"project_id": task.project_id, "task_id": task.id})
    return task_with_open_blockers(task)


@router.patch("/tasks/{task_id}", response_model=TaskRead)
async def update_task(task_id: int, payload: TaskUpdate, _: Writable, db: Session = Depends(get_db)):
    task = db.query(Task).options(joinedload(Task.blockers), joinedload(Task.children)).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")

    updates = payload.model_dump(exclude_unset=True)
    prospective_parent = updates.get("parent_task_id", task.parent_task_id)
    prospective_team = updates.get("team_id", task.team_id)
    if prospective_team != task.team_id:
        for child in task.children:
            child.parent_task_id = None
        if "parent_task_id" not in updates:
            prospective_parent = None
            task.parent_task_id = None
    shadow = Task(project_id=task.project_id, team_id=prospective_team)
    shadow.id = task.id
    validate_parent_task(db, shadow, prospective_parent)
    for field, new_value in updates.items():
        old_value = getattr(task, field)
        if old_value != new_value:
            db.add(
                TaskAuditLog(
                    task_id=task.id,
                    field=field,
                    old_value=str(old_value),
                    new_value=str(new_value),
                )
            )
            setattr(task, field, new_value)

    db.commit()
    db.refresh(task)
    await manager.broadcast("task.updated", {"project_id": task.project_id, "task_id": task.id})
    return task_with_open_blockers(task)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: int, _: Writable, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    project_id = task.project_id
    dependents = db.query(Task).filter(Task.project_id == project_id).all()
    for dependent in dependents:
        dependencies = dependent.dependencies or []
        if task_id in dependencies:
            dependent.dependencies = [dependency for dependency in dependencies if dependency != task_id]
        if dependent.parent_task_id == task_id:
            dependent.parent_task_id = None
    db.delete(task)
    db.commit()
    await manager.broadcast("task.deleted", {"project_id": project_id, "task_id": task_id})
    return Response(status_code=204)


@router.get("/tasks/{task_id}/audit", response_model=list[TaskAuditRead])
def task_audit(task_id: int, _: Protected, db: Session = Depends(get_db)):
    return db.query(TaskAuditLog).filter(TaskAuditLog.task_id == task_id).order_by(TaskAuditLog.changed_at.desc()).all()


@router.get("/projects/{project_id}/tasks/export.csv")
def export_tasks(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(Task).filter(Task.project_id == project_id)
    if team_id:
        query = query.filter(Task.team_id == team_id)
    tasks = query.order_by(Task.id).all()
    rows = [
        {
            "id": task.id,
            "team_id": task.team_id,
            "parent_task_id": task.parent_task_id,
            "title": task.title,
            "owner": task.owner,
            "status": task.status,
            "priority": task.priority,
            "start_date": task.start_date,
            "due_date": task.due_date,
            "dependencies": ",".join(str(dep) for dep in (task.dependencies or [])),
            "progress": task.progress,
        }
        for task in tasks
    ]
    return write_csv(rows, list(rows[0].keys()) if rows else ["id"], f"robotx-project-{project_id}-tasks.csv")


@router.get("/projects/{project_id}/blockers", response_model=list[BlockerRead])
def list_blockers(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    query = (
        db.query(Blocker)
        .join(Task)
        .filter(Task.project_id == project_id)
    )
    if team_id:
        query = query.filter(Task.team_id == team_id)
    blockers = query.order_by(Blocker.status, Blocker.created_at.desc()).all()
    return [
        {
            "id": blocker.id,
            "task_id": blocker.task_id,
            "description": blocker.description,
            "severity": blocker.severity,
            "status": blocker.status,
            "created_at": blocker.created_at,
            "task_title": blocker.task.title,
            "team_id": blocker.task.team_id,
            "team_code": blocker.task.team.code if blocker.task.team else None,
        }
        for blocker in blockers
    ]


@router.post("/blockers", response_model=BlockerRead)
async def create_blocker(payload: BlockerCreate, _: Writable, db: Session = Depends(get_db)):
    task = db.get(Task, payload.task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    blocker = Blocker(**payload.model_dump())
    db.add(blocker)
    if payload.status == "open" and task.status != "blocked":
        db.add(
            TaskAuditLog(
                task_id=task.id,
                field="status",
                old_value=task.status,
                new_value="blocked",
                changed_by="blocker-system",
            )
        )
        task.status = "blocked"
    db.commit()
    db.refresh(blocker)
    await manager.broadcast("blocker.updated", {"project_id": task.project_id, "task_id": task.id})
    return {**payload.model_dump(), "id": blocker.id, "created_at": blocker.created_at, "task_title": task.title}


@router.patch("/blockers/{blocker_id}", response_model=BlockerRead)
async def update_blocker(blocker_id: int, payload: BlockerUpdate, _: Writable, db: Session = Depends(get_db)):
    blocker = db.query(Blocker).options(joinedload(Blocker.task)).filter(Blocker.id == blocker_id).first()
    if not blocker:
        raise HTTPException(404, "Blocker not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(blocker, field, value)
    if blocker.status == "resolved":
        open_blockers = (
            db.query(Blocker)
            .filter(Blocker.task_id == blocker.task_id, Blocker.status == "open", Blocker.id != blocker.id)
            .count()
        )
        if open_blockers == 0 and blocker.task.status == "blocked":
            db.add(
                TaskAuditLog(
                    task_id=blocker.task.id,
                    field="status",
                    old_value="blocked",
                    new_value="in_progress",
                    changed_by="blocker-system",
                )
            )
            blocker.task.status = "in_progress"
    db.commit()
    db.refresh(blocker)
    await manager.broadcast("blocker.updated", {"project_id": blocker.task.project_id, "task_id": blocker.task_id})
    return {
        "id": blocker.id,
        "task_id": blocker.task_id,
        "description": blocker.description,
        "severity": blocker.severity,
        "status": blocker.status,
        "created_at": blocker.created_at,
        "task_title": blocker.task.title,
    }


@router.get("/projects/{project_id}/bom", response_model=list[BOMItemRead])
def list_bom(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    query = db.query(BOMItem).filter(BOMItem.project_id == project_id)
    if team_id:
        query = query.filter(BOMItem.team_id == team_id)
    items = query.order_by(BOMItem.category, BOMItem.name).all()
    changed = False
    for item in items:
        changed = normalize_bom_item_versions(db, item) or changed
    if changed:
        db.commit()
    return items


@router.post("/bom", response_model=BOMItemRead)
async def create_bom_item(payload: BOMItemCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    item = BOMItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    await manager.broadcast("bom.updated", {"project_id": item.project_id, "bom_item_id": item.id})
    return item


@router.patch("/bom/{item_id}", response_model=BOMItemRead)
async def update_bom_item(item_id: int, payload: BOMItemUpdate, _: Writable, db: Session = Depends(get_db)):
    item = db.get(BOMItem, item_id)
    if not item:
        raise HTTPException(404, "BOM item not found")
    normalize_bom_item_versions(db, item)
    db.add(
        BOMVersion(
            bom_item_id=item.id,
            category=item.category,
            name=item.name,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            sponsored_by=item.sponsored_by,
            version=item.version,
            note=payload.note,
        )
    )
    updates = payload.model_dump(exclude_unset=True, exclude={"note"})
    for field, value in updates.items():
        setattr(item, field, value)
    item.version += 1
    item.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(item)
    await manager.broadcast("bom.updated", {"project_id": item.project_id, "bom_item_id": item.id})
    return item


@router.get("/bom/{item_id}/versions", response_model=list[BOMVersionRead])
def bom_versions(item_id: int, _: Protected, db: Session = Depends(get_db)):
    return db.query(BOMVersion).filter(BOMVersion.bom_item_id == item_id).order_by(BOMVersion.version.desc()).all()


@router.delete("/bom/{item_id}", status_code=204)
async def delete_bom_item(item_id: int, _: Writable, db: Session = Depends(get_db)):
    item = db.get(BOMItem, item_id)
    if not item:
        raise HTTPException(404, "BOM item not found")
    project_id = item.project_id
    db.delete(item)
    db.commit()
    await manager.broadcast("bom.updated", {"project_id": project_id})
    return Response(status_code=204)


@router.delete("/bom-version/{version_id}", status_code=204)
async def delete_bom_version(version_id: int, _: Writable, db: Session = Depends(get_db)):
    version = db.get(BOMVersion, version_id)
    if not version:
        raise HTTPException(404, "BOM version not found")
    item = version.bom_item
    project_id = item.project_id
    db.delete(version)
    db.flush()
    normalize_bom_item_versions(db, item)
    db.commit()
    await manager.broadcast("bom.updated", {"project_id": project_id})
    return Response(status_code=204)


@router.post("/bom/{item_id}/rollback/{version_id}", response_model=BOMItemRead)
async def rollback_bom(item_id: int, version_id: int, _: Writable, db: Session = Depends(get_db)):
    item = db.get(BOMItem, item_id)
    version = db.get(BOMVersion, version_id)
    if not item or not version or version.bom_item_id != item_id:
        raise HTTPException(404, "BOM item version not found")
    normalize_bom_item_versions(db, item)
    item.name = version.name
    item.category = version.category
    item.quantity = version.quantity
    item.unit_cost = version.unit_cost
    item.sponsored_by = version.sponsored_by
    item.version = version.version
    item.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(item)
    await manager.broadcast("bom.updated", {"project_id": item.project_id, "bom_item_id": item.id})
    return item


@router.get("/projects/{project_id}/bom/export.csv")
def export_bom(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(BOMItem).filter(BOMItem.project_id == project_id)
    if team_id:
        query = query.filter(BOMItem.team_id == team_id)
    items = query.order_by(BOMItem.id).all()
    rows = [
        {
            "id": item.id,
            "team_id": item.team_id,
            "category": item.category,
            "name": item.name,
            "quantity": item.quantity,
            "unit_cost": item.unit_cost,
            "total_cost": round(item.quantity * item.unit_cost, 2),
            "sponsored_by": item.sponsored_by,
            "version": item.version,
            "last_updated": item.last_updated,
        }
        for item in items
    ]
    return write_csv(rows, list(rows[0].keys()) if rows else ["id"], f"robotx-project-{project_id}-bom.csv")


@router.get("/projects/{project_id}/budget", response_model=list[BudgetLogRead])
def list_budget_logs(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    query = db.query(BudgetLog).filter(BudgetLog.project_id == project_id)
    if team_id:
        query = query.filter(BudgetLog.team_id == team_id)
    return query.order_by(BudgetLog.date.desc()).all()


@router.post("/budget", response_model=BudgetLogRead)
async def create_budget_log(payload: BudgetLogCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    log = BudgetLog(**payload.model_dump())
    db.add(log)
    db.commit()
    db.refresh(log)
    await manager.broadcast("budget.updated", {"project_id": log.project_id})
    return log


@router.patch("/budget/{log_id}", response_model=BudgetLogRead)
async def update_budget_log(log_id: int, payload: BudgetLogUpdate, _: Writable, db: Session = Depends(get_db)):
    log = db.get(BudgetLog, log_id)
    if not log:
        raise HTTPException(404, "Budget log not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(log, field, value)
    db.commit()
    db.refresh(log)
    await manager.broadcast("budget.updated", {"project_id": log.project_id, "budget_log_id": log.id})
    return log


@router.delete("/budget/{log_id}", status_code=204)
async def delete_budget_log(log_id: int, _: Writable, db: Session = Depends(get_db)):
    log = db.get(BudgetLog, log_id)
    if not log:
        raise HTTPException(404, "Budget log not found")
    project_id = log.project_id
    db.delete(log)
    db.commit()
    await manager.broadcast("budget.updated", {"project_id": project_id})
    return Response(status_code=204)


@router.get("/projects/{project_id}/sponsors", response_model=list[SponsorRead])
def list_sponsors(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    query = db.query(Sponsor).filter(Sponsor.project_id == project_id)
    if team_id:
        query = query.filter(Sponsor.team_id == team_id)
    return query.order_by(Sponsor.date.desc(), Sponsor.id.desc()).all()


@router.post("/sponsors", response_model=SponsorRead)
async def create_sponsor(payload: SponsorCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    sponsor = Sponsor(**payload.model_dump())
    db.add(sponsor)
    db.commit()
    db.refresh(sponsor)
    await manager.broadcast("sponsor.updated", {"project_id": sponsor.project_id, "sponsor_id": sponsor.id})
    return sponsor


@router.patch("/sponsors/{sponsor_id}", response_model=SponsorRead)
async def update_sponsor(sponsor_id: int, payload: SponsorUpdate, _: Writable, db: Session = Depends(get_db)):
    sponsor = db.get(Sponsor, sponsor_id)
    if not sponsor:
        raise HTTPException(404, "Sponsor not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(sponsor, field, value)
    db.commit()
    db.refresh(sponsor)
    await manager.broadcast("sponsor.updated", {"project_id": sponsor.project_id, "sponsor_id": sponsor.id})
    return sponsor


@router.delete("/sponsors/{sponsor_id}", status_code=204)
async def delete_sponsor(sponsor_id: int, _: Writable, db: Session = Depends(get_db)):
    sponsor = db.get(Sponsor, sponsor_id)
    if not sponsor:
        raise HTTPException(404, "Sponsor not found")
    project_id = sponsor.project_id
    db.delete(sponsor)
    db.commit()
    await manager.broadcast("sponsor.updated", {"project_id": project_id})
    return Response(status_code=204)


@router.get("/users", response_model=list[UserRead])
def list_users(_: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(User)
    if team_id:
        query = query.filter(User.team_id == team_id)
    return query.order_by(User.name).all()


@router.post("/users", response_model=UserRead)
async def create_user(payload: UserCreate, _: Writable, db: Session = Depends(get_db)):
    user = User(**payload.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    await manager.broadcast("user.updated", {"team_id": user.team_id, "user_id": user.id})
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(user_id: int, payload: UserUpdate, _: Writable, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Member not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    await manager.broadcast("user.updated", {"team_id": user.team_id, "user_id": user.id})
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, _: Writable, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Member not found")
    db.delete(user)
    db.commit()
    await manager.broadcast("user.updated", {"user_id": user_id})
    return Response(status_code=204)


@router.websocket("/ws")
async def websocket_updates(websocket: WebSocket):
    if not await require_websocket_token(websocket):
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
