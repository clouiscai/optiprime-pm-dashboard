from datetime import date as DateType, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ProjectBase(BaseModel):
    name: str
    description: str = ""
    start_date: DateType | None = None
    end_date: DateType | None = None
    budget: float = 0


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    start_date: DateType | None = None
    end_date: DateType | None = None
    budget: float | None = None


class ProjectDeleteRequest(BaseModel):
    admin_password: str
    confirm_password: str


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class TeamBase(BaseModel):
    project_id: int
    code: str
    name: str
    domain: str
    description: str = ""
    budget: float = 0


class TeamCreate(TeamBase):
    pass


class TeamUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    domain: str | None = None
    description: str | None = None
    budget: float | None = None


class TeamRead(TeamBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class TaskBase(BaseModel):
    project_id: int
    team_id: int | None = None
    parent_task_id: int | None = None
    title: str
    description: str = ""
    owner: str = ""
    status: str = "todo"
    priority: str = "medium"
    start_date: DateType | None = None
    due_date: DateType | None = None
    dependencies: list[int] = Field(default_factory=list)
    progress: int = Field(default=0, ge=0, le=100)


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    team_id: int | None = None
    parent_task_id: int | None = None
    title: str | None = None
    description: str | None = None
    owner: str | None = None
    status: str | None = None
    priority: str | None = None
    start_date: DateType | None = None
    due_date: DateType | None = None
    dependencies: list[int] | None = None
    progress: int | None = Field(default=None, ge=0, le=100)


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    open_blockers: int = 0
    child_count: int = 0
    is_parent: bool = False


class BlockerBase(BaseModel):
    task_id: int
    description: str
    severity: str = "medium"
    status: str = "open"


class BlockerCreate(BlockerBase):
    pass


class BlockerUpdate(BaseModel):
    description: str | None = None
    severity: str | None = None
    status: str | None = None


class BlockerRead(BlockerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    task_title: str | None = None
    team_id: int | None = None
    team_code: str | None = None


class BOMItemBase(BaseModel):
    project_id: int
    team_id: int | None = None
    category: str = ""
    product_number: str = ""
    product: str = ""
    vendor: str = ""
    name: str
    quantity: float = 1
    unit_cost: float = 0
    sponsored_by: str = ""
    finalized: bool = False


class BOMItemCreate(BOMItemBase):
    pass


class BOMItemUpdate(BaseModel):
    team_id: int | None = None
    category: str | None = None
    product_number: str | None = None
    product: str | None = None
    vendor: str | None = None
    name: str | None = None
    quantity: float | None = None
    unit_cost: float | None = None
    sponsored_by: str | None = None
    finalized: bool | None = None
    note: str = "Updated from RobotX web app"


class BOMItemRead(BOMItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    last_updated: datetime

    @computed_field
    @property
    def total_cost(self) -> float:
        return round(self.quantity * self.unit_cost, 2)


class BOMVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bom_item_id: int
    category: str = ""
    product_number: str = ""
    product: str = ""
    vendor: str = ""
    name: str
    quantity: float
    unit_cost: float
    sponsored_by: str = ""
    version: int
    changed_at: datetime
    note: str

    @computed_field
    @property
    def total_cost(self) -> float:
        return round(self.quantity * self.unit_cost, 2)


class BudgetLogBase(BaseModel):
    project_id: int
    team_id: int | None = None
    category: str
    currency: str = Field(default="SGD", min_length=3, max_length=3)
    quantity: float = Field(default=1, gt=0)
    original_amount: float | None = None
    exchange_rate_to_sgd: float = Field(default=1, gt=0)
    amount: float = 0
    date: DateType
    notes: str = ""
    sponsored_by: str = ""
    adjustment_mode: str = Field(default="amount", pattern="^(amount|percentage)$")
    adjustment_rate: float = Field(default=0, ge=0)
    inventory_category: str = Field(default="Unsorted", max_length=40)
    inventory_available: bool = True
    inventory_note: str = Field(default="", max_length=220)


class BudgetLogCreate(BudgetLogBase):
    pass


class BudgetLogUpdate(BaseModel):
    team_id: int | None = None
    team_ids: list[int] | None = None
    referenced_item_ids: list[int] | None = None
    category: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    quantity: float | None = Field(default=None, gt=0)
    original_amount: float | None = None
    exchange_rate_to_sgd: float | None = Field(default=None, gt=0)
    amount: float | None = None
    date: DateType | None = None
    notes: str | None = None
    sponsored_by: str | None = None
    adjustment_mode: str | None = Field(default=None, pattern="^(amount|percentage)$")
    adjustment_rate: float | None = Field(default=None, ge=0)
    inventory_category: str | None = Field(default=None, max_length=40)
    inventory_available: bool | None = None
    inventory_note: str | None = Field(default=None, max_length=220)


class BudgetLogRead(BudgetLogBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int | None = None
    team_ids: list[int] = Field(default_factory=list)
    referenced_item_ids: list[int] = Field(default_factory=list)


class InvoicePurchaseCreate(BaseModel):
    category: str = Field(min_length=1, max_length=120)
    quantity: float = Field(default=1, gt=0)
    original_amount: float
    notes: str = ""
    team_ids: list[int] = Field(default_factory=list)
    referenced_item_ids: list[int] = Field(default_factory=list)
    adjustment_mode: str = Field(default="amount", pattern="^(amount|percentage)$")
    adjustment_rate: float = Field(default=0, ge=0)


class InvoiceUpdate(BaseModel):
    vendor: str | None = Field(default=None, min_length=1, max_length=160)
    invoice_number: str | None = Field(default=None, max_length=120)
    sponsored_by: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=220)
    invoice_date: DateType | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rate_to_sgd: float | None = Field(default=None, gt=0)


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    budget_log_id: int | None = None
    vendor: str
    invoice_number: str
    sponsored_by: str = ""
    description: str
    invoice_date: DateType | None = None
    currency: str = "SGD"
    original_amount: float = 0
    exchange_rate_to_sgd: float = 1
    amount_sgd: float = 0
    original_filename: str
    has_pdf: bool = False
    uploaded_at: datetime
    purchases: list[BudgetLogRead] = Field(default_factory=list)


class SponsorBase(BaseModel):
    project_id: int
    team_id: int | None = None
    name: str
    amount: float
    date: DateType
    notes: str = ""


class SponsorCreate(SponsorBase):
    pass


class SponsorUpdate(BaseModel):
    team_id: int | None = None
    name: str | None = None
    amount: float | None = None
    date: DateType | None = None
    notes: str | None = None


class SponsorRead(SponsorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class AssetBase(BaseModel):
    project_id: int
    team_id: int | None = None
    category: str = ""
    name: str
    asset_tag: str = ""
    source: str = "owned"
    provider: str = ""
    quantity: float = 1
    estimated_value: float = 0
    condition: str = ""
    location: str = ""
    assigned_to: str = ""
    start_date: DateType | None = None
    end_date: DateType | None = None
    notes: str = ""


class AssetCreate(AssetBase):
    pass


class AssetUpdate(BaseModel):
    team_id: int | None = None
    category: str | None = None
    name: str | None = None
    asset_tag: str | None = None
    source: str | None = None
    provider: str | None = None
    quantity: float | None = None
    estimated_value: float | None = None
    condition: str | None = None
    location: str | None = None
    assigned_to: str | None = None
    start_date: DateType | None = None
    end_date: DateType | None = None
    notes: str | None = None


class AssetRead(AssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class UserBase(BaseModel):
    team_id: int | None = None
    name: str
    role: str = "engineer"


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    team_id: int | None = None
    name: str | None = None
    role: str | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class TaskAuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    changed_at: datetime
    changed_by: str
    field: str
    old_value: str
    new_value: str


class DashboardRead(BaseModel):
    project: ProjectRead
    scope: str = "master"
    team: TeamRead | None = None
    completion: float
    active_blockers: int
    overdue_tasks: int
    total_tasks: int
    done_tasks: int
    bom_total: float
    budget_log_total: float
    sponsor_total: float = 0
    planned_budget: float = 0
    expected_spend: float = 0
    actual_spend: float
    remaining_budget: float
    unallocated_budget: float = 0
    unallocated_actual_spend: float = 0
    unallocated_remaining: float = 0
    status_counts: dict[str, int]
    priority_counts: dict[str, int]
    team_summaries: list[dict[str, Any]] = Field(default_factory=list)


class TokenCheck(BaseModel):
    ok: bool
    user: str = "OptiPrime"
    role: str = "admin"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    token: str
    user: str = "OptiPrime"
    role: str = "admin"


class RealtimeEvent(BaseModel):
    type: str
    payload: dict[str, Any]
