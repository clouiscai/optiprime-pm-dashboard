import base64
import csv
import io
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from uuid import uuid4
from datetime import date as DateType, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, joinedload

from database.session import get_db
from models.entities import Asset, Blocker, BOMItem, BOMVersion, BudgetLog, BudgetLogReference, BudgetLogTeam, Invoice, Project, Sponsor, Task, TaskAuditLog, Team, User
from models.schemas import (
    AssetCreate,
    AssetRead,
    AssetUpdate,
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
    InvoicePurchaseCreate,
    InvoiceRead,
    InvoiceUpdate,
    DashboardRead,
    LoginRequest,
    LoginResponse,
    ProjectCreate,
    ProjectDeleteRequest,
    ProjectRead,
    ProjectUpdate,
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
from services.auth import account_for_credentials, expected_username, require_token, require_websocket_token, require_write_token, role_for_token, session_for_token
from services.calculations import project_dashboard, task_with_open_blockers
from services.finance import is_adjustment_category, is_discount_category, project_purchase_allocations, purchase_allocation_weights
from services.realtime import manager


router = APIRouter()
logger = logging.getLogger("optiprime.api")
Protected = Annotated[str, Depends(require_token)]
Writable = Annotated[str, Depends(require_write_token)]
ROOT = Path(__file__).resolve().parents[1]
INVOICE_DIR = ROOT / "uploads" / "invoices"
MAX_INVOICE_BYTES = 10 * 1024 * 1024
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_ATTEMPT_LIMIT = 10
login_attempts: dict[str, deque[float]] = defaultdict(deque)
login_attempts_lock = Lock()


def login_client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def check_login_rate_limit(client_key: str):
    now = time.time()
    with login_attempts_lock:
        attempts = login_attempts[client_key]
        while attempts and attempts[0] <= now - LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
            retry_after = max(1, int(LOGIN_WINDOW_SECONDS - (now - attempts[0])))
            raise HTTPException(429, "Too many login attempts. Try again later.", headers={"Retry-After": str(retry_after)})


def record_login_result(client_key: str, succeeded: bool):
    with login_attempts_lock:
        if succeeded:
            login_attempts.pop(client_key, None)
        else:
            login_attempts[client_key].append(time.time())


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def validate_project_team(db: Session, project_id: int, team_id: int | None) -> int | None:
    if team_id is None:
        return None
    team = db.get(Team, team_id)
    if not team or team.project_id != project_id:
        raise HTTPException(400, "Team must belong to the invoice project")
    return team.id


def validate_project_team_ids(db: Session, project_id: int, team_ids: list[int]) -> list[int]:
    normalized = sorted(set(team_ids))
    if not normalized:
        return []
    valid_ids = {
        team_id
        for (team_id,) in db.query(Team.id).filter(Team.project_id == project_id, Team.id.in_(normalized)).all()
    }
    if valid_ids != set(normalized):
        raise HTTPException(400, "Every selected team must belong to the invoice project")
    return normalized


def configure_purchase_scope(
    db: Session,
    purchase: BudgetLog,
    team_ids: list[int],
    referenced_item_ids: list[int],
) -> None:
    validated_team_ids = validate_project_team_ids(db, purchase.project_id, team_ids)
    normalized_references = sorted(set(referenced_item_ids))
    adjustment = is_adjustment_category(purchase.category)

    if adjustment and not normalized_references:
        raise HTTPException(400, "Tax and discount lines must reference at least one invoice item")
    if adjustment and purchase.adjustment_mode == "percentage" and purchase.adjustment_rate <= 0:
        raise HTTPException(400, "Percentage adjustments require a rate greater than zero")
    if adjustment and validated_team_ids:
        raise HTTPException(400, "Tax and discount team allocation is inherited from the referenced items")
    if not adjustment and normalized_references:
        raise HTTPException(400, "Only tax and discount lines can reference invoice items")
    if not adjustment and purchase.adjustment_mode != "amount":
        raise HTTPException(400, "Only tax and discount lines can use percentage mode")

    if normalized_references:
        targets = (
            db.query(BudgetLog)
            .filter(
                BudgetLog.id.in_(normalized_references),
                BudgetLog.invoice_id == purchase.invoice_id,
                BudgetLog.project_id == purchase.project_id,
                BudgetLog.id != purchase.id,
            )
            .all()
        )
        if len(targets) != len(normalized_references):
            raise HTTPException(400, "Referenced items must belong to the same invoice")
        if any(is_adjustment_category(target.category) for target in targets):
            raise HTTPException(400, "Tax and discount lines must reference purchase items, not other adjustments")

    purchase.team_allocations = [BudgetLogTeam(team_id=team_id) for team_id in validated_team_ids]
    purchase.reference_links = [
        BudgetLogReference(target_log_id=target_id)
        for target_id in normalized_references
    ]
    purchase.team_id = validated_team_ids[0] if len(validated_team_ids) == 1 else None


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


def normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(400, "Currency must be a three-letter code such as SGD, USD, or EUR")
    return currency


def amount_in_sgd(original_amount: float, exchange_rate_to_sgd: float) -> float:
    if exchange_rate_to_sgd <= 0:
        raise HTTPException(400, "Exchange rate must be greater than zero")
    converted = Decimal(str(original_amount)) * Decimal(str(exchange_rate_to_sgd))
    return float(converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def refresh_invoice_totals(db: Session, invoice: Invoice) -> None:
    db.flush()
    purchases = (
        db.query(BudgetLog)
        .options(joinedload(BudgetLog.reference_links))
        .filter(BudgetLog.invoice_id == invoice.id)
        .order_by(BudgetLog.id)
        .all()
    )
    purchases_by_id = {purchase.id: purchase for purchase in purchases}

    for purchase in purchases:
        adjustment = is_adjustment_category(purchase.category)
        if adjustment and purchase.adjustment_mode == "percentage":
            referenced_total = sum(
                abs(target.quantity * target.original_amount)
                for target_id in purchase.referenced_item_ids
                if (target := purchases_by_id.get(target_id)) is not None
                and not is_adjustment_category(target.category)
            )
            computed_total = round(referenced_total * abs(purchase.adjustment_rate) / 100, 2)
            purchase.quantity = 1
            purchase.original_amount = -computed_total if is_discount_category(purchase.category) else computed_total
        elif adjustment:
            purchase.adjustment_mode = "amount"
            purchase.adjustment_rate = 0
            purchase.original_amount = (
                -abs(purchase.original_amount)
                if is_discount_category(purchase.category)
                else abs(purchase.original_amount)
            )
        else:
            purchase.adjustment_mode = "amount"

        purchase.amount = amount_in_sgd(
            purchase.original_amount * purchase.quantity,
            purchase.exchange_rate_to_sgd,
        )

    invoice.original_amount = round(sum(purchase.quantity * purchase.original_amount for purchase in purchases), 2)
    invoice.amount_sgd = round(sum(purchase.amount for purchase in purchases), 2)


def validate_invoice_identity(
    db: Session,
    project_id: int,
    vendor: str,
    invoice_number: str,
    exclude_invoice_id: int | None = None,
) -> tuple[str, str]:
    clean_vendor = vendor.strip()
    clean_number = invoice_number.strip()
    if not clean_vendor:
        raise HTTPException(400, "Vendor is required")
    if not clean_number:
        return clean_vendor, ""
    query = db.query(Invoice).filter(
        Invoice.project_id == project_id,
        Invoice.vendor.ilike(clean_vendor),
        Invoice.invoice_number.ilike(clean_number),
    )
    if exclude_invoice_id is not None:
        query = query.filter(Invoice.id != exclude_invoice_id)
    if query.first():
        raise HTTPException(409, "This vendor already has an invoice with that number")
    return clean_vendor, clean_number


async def read_invoice_pdf(file: UploadFile) -> tuple[str, bytes]:
    original_name = Path(file.filename or "invoice.pdf").name
    if file.content_type != "application/pdf" or Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(400, "Only PDF invoices are accepted")
    content = await file.read(MAX_INVOICE_BYTES + 1)
    if len(content) > MAX_INVOICE_BYTES:
        raise HTTPException(413, "Invoice PDF must be 10 MB or smaller")
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "Only valid PDF invoices are accepted")
    return original_name, content


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


def ensure_invoice_file_data_column(db: Session):
    if os.getenv("OPTIPRIME_SKIP_STARTUP_DB", "").lower() in {"1", "true", "yes"}:
        return
    try:
        if db.bind and db.bind.dialect.name == "sqlite":
            columns = [row[1] for row in db.execute(text("PRAGMA table_info(invoices)")).all()]
            if "file_data" not in columns:
                db.execute(text("ALTER TABLE invoices ADD COLUMN file_data TEXT DEFAULT '' NOT NULL"))
        else:
            db.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS file_data TEXT DEFAULT '' NOT NULL"))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to ensure invoice file_data column")
        raise


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


def status_for_progress(progress: int, current_status: str) -> str:
    if current_status == "blocked":
        return current_status
    if progress >= 100:
        return "done"
    if progress <= 0:
        return "todo"
    return "in_progress"


@router.get("/auth/verify", response_model=TokenCheck)
def verify_token(token: Protected):
    session = session_for_token(token)
    role = role_for_token(token) or "viewer"
    fallback_user = expected_username() if role == "admin" else "viewer"
    return TokenCheck(ok=True, user=str(session["sub"]) if session else fallback_user, role=role)


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    client_key = login_client_key(request)
    check_login_rate_limit(client_key)
    try:
        account = account_for_credentials(payload.username, payload.password)
    except RuntimeError as exc:
        logger.error("Authentication configuration is incomplete")
        raise HTTPException(503, "Authentication is temporarily unavailable") from exc
    if not account:
        record_login_result(client_key, False)
        raise HTTPException(401, "Invalid username or password")
    record_login_result(client_key, True)
    response.headers["Cache-Control"] = "no-store"
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


@router.patch("/projects/{project_id}", response_model=ProjectRead)
async def update_project(project_id: int, payload: ProjectUpdate, _: Writable, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    await manager.broadcast("project.updated", {"project_id": project.id})
    return project


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: int, payload: ProjectDeleteRequest, _: Writable, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    if payload.admin_password != payload.confirm_password:
        raise HTTPException(400, "Admin passwords do not match")
    account = account_for_credentials(expected_username(), payload.admin_password)
    if not account or account["role"] != "admin":
        raise HTTPException(403, "Project admin authentication failed")

    team_ids = [team_id for (team_id,) in db.query(Team.id).filter(Team.project_id == project_id).all()]
    task_ids = [task_id for (task_id,) in db.query(Task.id).filter(Task.project_id == project_id).all()]
    bom_item_ids = [item_id for (item_id,) in db.query(BOMItem.id).filter(BOMItem.project_id == project_id).all()]

    if task_ids:
        db.query(Blocker).filter(Blocker.task_id.in_(task_ids)).delete(synchronize_session=False)
        db.query(TaskAuditLog).filter(TaskAuditLog.task_id.in_(task_ids)).delete(synchronize_session=False)
    if bom_item_ids:
        db.query(BOMVersion).filter(BOMVersion.bom_item_id.in_(bom_item_ids)).delete(synchronize_session=False)
    if team_ids:
        db.query(User).filter(User.team_id.in_(team_ids)).delete(synchronize_session=False)

    for model in (Invoice, Asset, Sponsor, BudgetLog, BOMItem, Task, Team):
        db.query(model).filter(model.project_id == project_id).delete(synchronize_session=False)
    db.delete(project)
    db.commit()
    await manager.broadcast("project.deleted", {"project_id": project_id})
    return Response(status_code=204)


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


@router.delete("/teams/{team_id}", status_code=204)
async def delete_team(team_id: int, _: Writable, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    project_id = team.project_id
    for model in (Task, BOMItem, BudgetLog, Sponsor, Asset, User, Invoice):
        db.query(model).filter(model.team_id == team_id).update({model.team_id: None}, synchronize_session=False)
    db.delete(team)
    db.commit()
    await manager.broadcast("team.updated", {"project_id": project_id, "team_id": team_id})
    return Response(status_code=204)


@router.get("/projects/{project_id}/dashboard", response_model=DashboardRead)
def get_dashboard(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    return project_dashboard(db, project, team_id)


@router.get("/projects/{project_id}/tasks", response_model=list[TaskRead])
def list_tasks(project_id: int, _: Protected, team_id: int | None = None, general: bool = False, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    query = (
        db.query(Task)
        .options(joinedload(Task.blockers), joinedload(Task.children))
        .filter(Task.project_id == project_id)
    )
    if general:
        query = query.filter(Task.team_id.is_(None))
    elif team_id:
        query = query.filter(Task.team_id == team_id)
    tasks = query.order_by(Task.due_date.is_(None), Task.due_date, Task.id).all()
    return [task_with_open_blockers(task) for task in tasks]


@router.post("/tasks", response_model=TaskRead)
async def create_task(payload: TaskCreate, _: Writable, db: Session = Depends(get_db)):
    try:
        get_project_or_404(db, payload.project_id)
        task = Task(**payload.model_dump())
        validate_parent_task(db, task, task.parent_task_id)
        db.add(task)
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        logger.exception("Failed to create task")
        raise
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
    if "progress" in updates:
        requested_status = updates.get("status", task.status)
        updates["status"] = status_for_progress(int(updates["progress"] or 0), requested_status)
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

    try:
        db.commit()
        db.refresh(task)
    except Exception:
        db.rollback()
        logger.exception("Failed to update task %s", task_id)
        raise
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
def export_tasks(project_id: int, _: Protected, team_id: int | None = None, general: bool = False, db: Session = Depends(get_db)):
    query = db.query(Task).filter(Task.project_id == project_id)
    if general:
        query = query.filter(Task.team_id.is_(None))
    elif team_id:
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
        query = query.filter(or_(BOMItem.team_id == team_id, BOMItem.team_id.is_(None)))
    return query.order_by(BOMItem.category, BOMItem.product, BOMItem.name).all()


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
            product_number=item.product_number,
            product=item.product,
            vendor=item.vendor,
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


@router.post("/bom/{item_id}/finalize", response_model=BOMItemRead)
async def finalize_bom_item(item_id: int, _: Writable, db: Session = Depends(get_db)):
    item = db.get(BOMItem, item_id)
    if not item:
        raise HTTPException(404, "BOM item not found")
    item.finalized = True
    item.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(item)
    await manager.broadcast("bom.updated", {"project_id": item.project_id, "bom_item_id": item.id})
    return item


@router.post("/bom/{item_id}/reopen", response_model=BOMItemRead)
async def reopen_bom_item(item_id: int, _: Writable, db: Session = Depends(get_db)):
    item = db.get(BOMItem, item_id)
    if not item:
        raise HTTPException(404, "BOM item not found")
    item.finalized = False
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
    item.product_number = version.product_number
    item.product = version.product
    item.vendor = version.vendor
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
    query = db.query(BOMItem).options(joinedload(BOMItem.team)).filter(BOMItem.project_id == project_id)
    if team_id:
        query = query.filter(or_(BOMItem.team_id == team_id, BOMItem.team_id.is_(None)))
    items = query.order_by(BOMItem.id).all()
    rows = [
        {
            "id": item.id,
            "team": item.team.code if item.team else "General",
            "category": item.category,
            "product_number": item.product_number,
            "product": item.product,
            "vendor": item.vendor,
            "description": item.name,
            "quantity": item.quantity,
            "unit_cost": item.unit_cost,
            "total_cost": round(item.quantity * item.unit_cost, 2),
            "sponsored_by": item.sponsored_by,
            "version": f"V{item.version}" if item.finalized else f"v{item.version}",
            "finalized": item.finalized,
            "last_updated": item.last_updated,
        }
        for item in items
    ]
    return write_csv(rows, list(rows[0].keys()) if rows else ["id"], f"robotx-project-{project_id}-bom.csv")


@router.get("/projects/{project_id}/budget", response_model=list[BudgetLogRead])
def list_budget_logs(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    logs = (
        db.query(BudgetLog)
        .options(joinedload(BudgetLog.team_allocations), joinedload(BudgetLog.reference_links))
        .filter(BudgetLog.project_id == project_id)
        .order_by(BudgetLog.date.desc())
        .all()
    )
    if not team_id:
        return logs
    validate_project_team(db, project_id, team_id)
    allocations = project_purchase_allocations(logs)
    return [log for log in logs if team_id in allocations.get(log.id, {})]


@router.post("/budget", response_model=BudgetLogRead)
async def create_budget_log(payload: BudgetLogCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    values = payload.model_dump()
    original_amount = payload.original_amount if payload.original_amount is not None else payload.amount
    values["currency"] = normalize_currency(payload.currency)
    values["original_amount"] = original_amount
    values["amount"] = amount_in_sgd(original_amount * payload.quantity, payload.exchange_rate_to_sgd)
    log = BudgetLog(**values)
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
    updates = payload.model_dump(exclude_unset=True)
    requested_team_ids = updates.pop("team_ids", None)
    requested_references = updates.pop("referenced_item_ids", None)
    linked_invoice = db.get(Invoice, log.invoice_id) if log.invoice_id else None
    currency = normalize_currency(linked_invoice.currency if linked_invoice else updates.get("currency", log.currency))
    exchange_rate = linked_invoice.exchange_rate_to_sgd if linked_invoice else updates.get("exchange_rate_to_sgd", log.exchange_rate_to_sgd)
    original_amount = updates.get("original_amount")
    if original_amount is None:
        original_amount = updates.get("amount", log.original_amount or log.amount)
    updates["currency"] = currency
    updates["original_amount"] = original_amount
    updates["exchange_rate_to_sgd"] = exchange_rate
    quantity = updates.get("quantity", log.quantity)
    updates["quantity"] = quantity
    updates["amount"] = amount_in_sgd(original_amount * quantity, exchange_rate)
    if "inventory_unavailable_quantity" in updates:
        unavailable_quantity = updates["inventory_unavailable_quantity"]
        if unavailable_quantity > quantity:
            raise HTTPException(400, "Out-of-service quantity cannot exceed the purchased quantity")
        updates["inventory_available"] = unavailable_quantity < quantity
    elif "inventory_available" in updates:
        updates["inventory_unavailable_quantity"] = 0 if updates["inventory_available"] else quantity
    elif quantity < log.inventory_unavailable_quantity:
        updates["inventory_unavailable_quantity"] = quantity
        updates["inventory_available"] = False
    if linked_invoice:
        updates["date"] = linked_invoice.invoice_date or log.date
        updates["sponsored_by"] = linked_invoice.sponsored_by
    for field, value in updates.items():
        setattr(log, field, value)
    if linked_invoice:
        adjustment = is_adjustment_category(log.category)
        team_ids = requested_team_ids if requested_team_ids is not None else ([] if adjustment else log.team_ids)
        references = requested_references if requested_references is not None else (log.referenced_item_ids if adjustment else [])
        configure_purchase_scope(db, log, team_ids, references)
    db.flush()
    if linked_invoice:
        refresh_invoice_totals(db, linked_invoice)
    db.commit()
    db.refresh(log)
    await manager.broadcast("budget.updated", {"project_id": log.project_id, "budget_log_id": log.id})
    return log


@router.delete("/budget/{log_id}", status_code=204)
async def delete_budget_log(log_id: int, _: Writable, db: Session = Depends(get_db)):
    log = db.get(BudgetLog, log_id)
    if not log:
        raise HTTPException(404, "Budget log not found")
    if db.query(BudgetLogReference).filter(BudgetLogReference.target_log_id == log_id).first():
        raise HTTPException(409, "This item is used by a tax or discount line. Remove that reference first.")
    project_id = log.project_id
    invoice_id = log.invoice_id
    linked_invoice = db.get(Invoice, invoice_id) if invoice_id else None
    db.query(Invoice).filter(Invoice.budget_log_id == log.id).update({Invoice.budget_log_id: None}, synchronize_session=False)
    db.delete(log)
    db.flush()
    if linked_invoice:
        refresh_invoice_totals(db, linked_invoice)
    db.commit()
    await manager.broadcast("budget.updated", {"project_id": project_id})
    if invoice_id:
        await manager.broadcast("invoice.updated", {"project_id": project_id, "invoice_id": invoice_id})
    return Response(status_code=204)


@router.get("/projects/{project_id}/invoices", response_model=list[InvoiceRead])
def list_invoices(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    ensure_invoice_file_data_column(db)
    query = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.purchases).joinedload(BudgetLog.team_allocations),
            joinedload(Invoice.purchases).joinedload(BudgetLog.reference_links),
        )
        .filter(Invoice.project_id == project_id)
    )
    invoices = query.order_by(Invoice.vendor.asc(), Invoice.invoice_date.desc(), Invoice.id.desc()).all()
    if team_id is not None:
        validate_project_team(db, project_id, team_id)
        invoices = [
            invoice
            for invoice in invoices
            if (
                any(
                    team_id in purchase_allocation_weights(purchase, {item.id: item for item in invoice.purchases})
                    for purchase in invoice.purchases
                )
            )
        ]
    return invoices


@router.post("/invoices", response_model=InvoiceRead)
async def upload_invoice(
    _: Writable,
    project_id: int = Form(...),
    vendor: str = Form(...),
    invoice_number: str = Form(""),
    description: str = Form(...),
    invoice_date: DateType = Form(...),
    currency: str = Form("SGD"),
    exchange_rate_to_sgd: float = Form(1),
    category: str = Form(""),
    original_amount: float = Form(0),
    sponsored_by: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    ensure_invoice_file_data_column(db)
    clean_vendor, clean_number = validate_invoice_identity(db, project_id, vendor, invoice_number)
    original_name, content = await read_invoice_pdf(file)

    clean_description = description.strip()
    if not clean_description or len(clean_description) > 220:
        raise HTTPException(400, "Invoice description must be between 1 and 220 characters")

    currency_code = normalize_currency(currency)
    amount_in_sgd(0, exchange_rate_to_sgd)
    clean_sponsor = sponsored_by.strip()
    stored_name = f"{uuid4().hex}.pdf"

    invoice = Invoice(
        project_id=project_id,
        team_id=None,
        budget_log_id=None,
        vendor=clean_vendor,
        invoice_number=clean_number,
        sponsored_by=clean_sponsor,
        description=clean_description,
        invoice_date=invoice_date,
        currency=currency_code,
        original_amount=0,
        exchange_rate_to_sgd=exchange_rate_to_sgd,
        amount_sgd=0,
        original_filename=original_name,
        stored_filename=str(Path(str(project_id)) / stored_name),
        file_data=base64.b64encode(content).decode("ascii"),
        uploaded_at=datetime.utcnow(),
    )
    db.add(invoice)
    db.flush()
    clean_category = category.strip()
    if original_amount > 0:
        if not clean_category or len(clean_category) > 120:
            raise HTTPException(400, "A category is required when importing an invoice total")
        log = BudgetLog(
            project_id=project_id,
            team_id=None,
            invoice_id=invoice.id,
            category=clean_category,
            currency=currency_code,
            original_amount=original_amount,
            exchange_rate_to_sgd=exchange_rate_to_sgd,
            amount=amount_in_sgd(original_amount, exchange_rate_to_sgd),
            date=invoice_date,
            notes=clean_description,
            sponsored_by=clean_sponsor,
        )
        db.add(log)
        db.flush()
        configure_purchase_scope(db, log, [], [])
        invoice.budget_log_id = log.id
        refresh_invoice_totals(db, invoice)
    db.commit()
    db.refresh(invoice)
    await manager.broadcast("invoice.updated", {"project_id": project_id, "invoice_id": invoice.id})
    return invoice


@router.post("/invoices/{invoice_id}/file", response_model=InvoiceRead)
async def replace_invoice_file(
    invoice_id: int,
    _: Writable,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    original_name, content = await read_invoice_pdf(file)
    old_path = (INVOICE_DIR / invoice.stored_filename).resolve() if invoice.stored_filename else None
    invoice.original_filename = original_name
    invoice.stored_filename = str(Path(str(invoice.project_id)) / f"{uuid4().hex}.pdf")
    invoice.file_data = base64.b64encode(content).decode("ascii")
    db.commit()
    db.refresh(invoice)
    if old_path and str(old_path).startswith(str(INVOICE_DIR.resolve())) and old_path.exists():
        old_path.unlink()
    await manager.broadcast("invoice.updated", {"project_id": invoice.project_id, "invoice_id": invoice.id})
    return invoice


@router.delete("/invoices/{invoice_id}/file", status_code=204)
async def delete_invoice_file(invoice_id: int, _: Writable, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    old_path = (INVOICE_DIR / invoice.stored_filename).resolve() if invoice.stored_filename else None
    invoice.original_filename = ""
    invoice.stored_filename = ""
    invoice.file_data = ""
    db.commit()
    if old_path and str(old_path).startswith(str(INVOICE_DIR.resolve())) and old_path.exists():
        old_path.unlink()
    await manager.broadcast("invoice.updated", {"project_id": invoice.project_id, "invoice_id": invoice.id})
    return Response(status_code=204)


@router.patch("/invoices/{invoice_id}", response_model=InvoiceRead)
async def update_invoice(invoice_id: int, payload: InvoiceUpdate, _: Writable, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    updates = payload.model_dump(exclude_unset=True)
    vendor = updates.get("vendor", invoice.vendor)
    invoice_number = updates.get("invoice_number", invoice.invoice_number)
    clean_vendor, clean_number = validate_invoice_identity(
        db,
        invoice.project_id,
        vendor,
        invoice_number,
        exclude_invoice_id=invoice.id,
    )
    updates["vendor"] = clean_vendor
    updates["invoice_number"] = clean_number
    if "description" in updates:
        updates["description"] = updates["description"].strip()
    if "currency" in updates:
        updates["currency"] = normalize_currency(updates["currency"])
    if "sponsored_by" in updates:
        updates["sponsored_by"] = updates["sponsored_by"].strip()
    for field, value in updates.items():
        setattr(invoice, field, value)
    for purchase in invoice.purchases:
        purchase.currency = invoice.currency
        purchase.exchange_rate_to_sgd = invoice.exchange_rate_to_sgd
        if invoice.invoice_date:
            purchase.date = invoice.invoice_date
        purchase.sponsored_by = invoice.sponsored_by
    db.flush()
    refresh_invoice_totals(db, invoice)
    db.commit()
    db.refresh(invoice)
    await manager.broadcast("invoice.updated", {"project_id": invoice.project_id, "invoice_id": invoice.id})
    await manager.broadcast("budget.updated", {"project_id": invoice.project_id})
    return invoice


@router.post("/invoices/{invoice_id}/purchases", response_model=BudgetLogRead)
async def create_invoice_purchase(
    invoice_id: int,
    payload: InvoicePurchaseCreate,
    _: Writable,
    db: Session = Depends(get_db),
):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    category = payload.category.strip()
    if not category:
        raise HTTPException(400, "Purchase category is required")
    purchase = BudgetLog(
        project_id=invoice.project_id,
        team_id=None,
        invoice_id=invoice.id,
        category=category,
        quantity=payload.quantity,
        currency=invoice.currency,
        original_amount=payload.original_amount,
        exchange_rate_to_sgd=invoice.exchange_rate_to_sgd,
        amount=amount_in_sgd(payload.original_amount * payload.quantity, invoice.exchange_rate_to_sgd),
        date=invoice.invoice_date or DateType.today(),
        notes=payload.notes.strip(),
        sponsored_by=invoice.sponsored_by,
        adjustment_mode=payload.adjustment_mode,
        adjustment_rate=payload.adjustment_rate,
    )
    db.add(purchase)
    db.flush()
    configure_purchase_scope(db, purchase, payload.team_ids, payload.referenced_item_ids)
    refresh_invoice_totals(db, invoice)
    db.commit()
    db.refresh(purchase)
    await manager.broadcast("invoice.updated", {"project_id": invoice.project_id, "invoice_id": invoice.id})
    await manager.broadcast("budget.updated", {"project_id": invoice.project_id, "budget_log_id": purchase.id})
    return purchase


@router.get("/invoices/{invoice_id}/file")
def view_invoice(invoice_id: int, _: Protected, db: Session = Depends(get_db)):
    ensure_invoice_file_data_column(db)
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if not invoice.has_pdf:
        raise HTTPException(404, "No PDF is attached to this invoice")
    safe_name = Path(invoice.original_filename).name.replace(chr(34), "").replace("\r", "").replace("\n", "")
    if invoice.file_data:
        try:
            content = base64.b64decode(invoice.file_data, validate=True)
        except Exception as exc:
            raise HTTPException(500, "Invoice file is corrupted") from exc
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{safe_name}"',
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )
    path = (INVOICE_DIR / invoice.stored_filename).resolve()
    if not str(path).startswith(str(INVOICE_DIR.resolve())) or not path.exists():
        raise HTTPException(404, "Invoice file was not migrated. Please delete and re-upload the PDF.")
    return Response(
        content=path.read_bytes(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/invoices/{invoice_id}", status_code=204)
async def delete_invoice(invoice_id: int, _: Writable, db: Session = Depends(get_db)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    project_id = invoice.project_id
    purchase_ids = [purchase.id for purchase in invoice.purchases]
    path = (INVOICE_DIR / invoice.stored_filename).resolve() if invoice.stored_filename else None
    invoice.budget_log_id = None
    db.flush()
    db.delete(invoice)
    db.commit()
    if path and str(path).startswith(str(INVOICE_DIR.resolve())) and path.exists():
        path.unlink()
    await manager.broadcast("invoice.updated", {"project_id": project_id, "invoice_id": invoice_id})
    if purchase_ids:
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


@router.get("/projects/{project_id}/assets", response_model=list[AssetRead])
def list_assets(project_id: int, _: Protected, team_id: int | None = None, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    query = db.query(Asset).filter(Asset.project_id == project_id)
    if team_id:
        query = query.filter(Asset.team_id == team_id)
    return query.order_by(Asset.category, Asset.name, Asset.id).all()


@router.post("/assets", response_model=AssetRead)
async def create_asset(payload: AssetCreate, _: Writable, db: Session = Depends(get_db)):
    get_project_or_404(db, payload.project_id)
    asset = Asset(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    await manager.broadcast("asset.updated", {"project_id": asset.project_id, "asset_id": asset.id})
    return asset


@router.patch("/assets/{asset_id}", response_model=AssetRead)
async def update_asset(asset_id: int, payload: AssetUpdate, _: Writable, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    db.commit()
    db.refresh(asset)
    await manager.broadcast("asset.updated", {"project_id": asset.project_id, "asset_id": asset.id})
    return asset


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: int, _: Writable, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    project_id = asset.project_id
    db.delete(asset)
    db.commit()
    await manager.broadcast("asset.updated", {"project_id": project_id})
    return Response(status_code=204)


@router.get("/users", response_model=list[UserRead])
def list_users(_: Protected, project_id: int | None = None, team_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(User)
    if project_id:
        get_project_or_404(db, project_id)
        query = query.join(Team, User.team_id == Team.id).filter(Team.project_id == project_id)
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
