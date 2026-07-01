from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.session import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[float] = mapped_column(Float, default=0.0)

    teams: Mapped[list["Team"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    bom_items: Mapped[list["BOMItem"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    budget_logs: Mapped[list["BudgetLog"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sponsors: Mapped[list["Sponsor"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    domain: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    budget: Mapped[float] = mapped_column(Float, default=0.0)

    project: Mapped[Project] = relationship(back_populates="teams")
    tasks: Mapped[list["Task"]] = relationship(back_populates="team")
    bom_items: Mapped[list["BOMItem"]] = relationship(back_populates="team")
    budget_logs: Mapped[list["BudgetLog"]] = relationship(back_populates="team")
    sponsors: Mapped[list["Sponsor"]] = relationship(back_populates="team")
    assets: Mapped[list["Asset"]] = relationship(back_populates="team")
    users: Mapped[list["User"]] = relationship(back_populates="team")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(32), default="todo", index=True)
    priority: Mapped[str] = mapped_column(String(32), default="medium", index=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    dependencies: Mapped[list[int]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=list)
    progress: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="tasks")
    team: Mapped[Team | None] = relationship(back_populates="tasks")
    parent: Mapped["Task | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Task"]] = relationship(back_populates="parent")
    blockers: Mapped[list["Blocker"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    audits: Mapped[list["TaskAuditLog"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class Blocker(Base):
    __tablename__ = "blockers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped[Task] = relationship(back_populates="blockers")


class BOMItem(Base):
    __tablename__ = "bom_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(120), default="")
    product_number: Mapped[str] = mapped_column(String(120), default="")
    product: Mapped[str] = mapped_column(String(220), default="")
    vendor: Mapped[str] = mapped_column(String(160), default="")
    name: Mapped[str] = mapped_column(String(220), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    sponsored_by: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="bom_items")
    team: Mapped[Team | None] = relationship(back_populates="bom_items")
    versions: Mapped[list["BOMVersion"]] = relationship(back_populates="bom_item", cascade="all, delete-orphan")


class BOMVersion(Base):
    __tablename__ = "bom_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bom_item_id: Mapped[int] = mapped_column(ForeignKey("bom_items.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(120), default="")
    product_number: Mapped[str] = mapped_column(String(120), default="")
    product: Mapped[str] = mapped_column(String(220), default="")
    vendor: Mapped[str] = mapped_column(String(160), default="")
    name: Mapped[str] = mapped_column(String(220), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    sponsored_by: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    note: Mapped[str] = mapped_column(Text, default="")

    bom_item: Mapped[BOMItem] = relationship(back_populates="versions")


class BudgetLog(Base):
    __tablename__ = "budget_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    sponsored_by: Mapped[str] = mapped_column(String(160), default="")

    project: Mapped[Project] = relationship(back_populates="budget_logs")
    team: Mapped[Team | None] = relationship(back_populates="budget_logs")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(220), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(220), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(260), nullable=False)
    file_data: Mapped[str] = mapped_column(Text, default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship()
    team: Mapped[Team | None] = relationship()


class Sponsor(Base):
    __tablename__ = "sponsors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="sponsors")
    team: Mapped[Team | None] = relationship(back_populates="sponsors")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(220), nullable=False)
    asset_tag: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(32), default="owned", index=True)
    provider: Mapped[str] = mapped_column(String(160), default="")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    estimated_value: Mapped[float] = mapped_column(Float, default=0.0)
    condition: Mapped[str] = mapped_column(String(80), default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    assigned_to: Mapped[str] = mapped_column(String(160), default="")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="assets")
    team: Mapped[Team | None] = relationship(back_populates="assets")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(80), default="engineer")

    team: Mapped[Team | None] = relationship(back_populates="users")


class TaskAuditLog(Base):
    __tablename__ = "task_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    changed_by: Mapped[str] = mapped_column(String(120), default="local-user")
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")

    task: Mapped[Task] = relationship(back_populates="audits")
