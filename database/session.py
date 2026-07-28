import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA_DIR = ROOT / "database"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'robotx.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine_options = {"connect_args": connect_args, "future": True, "pool_pre_ping": True}
if DATABASE_URL.startswith("postgresql"):
    engine_options.update({"pool_size": 1, "max_overflow": 2, "pool_recycle": 300})
engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
_runtime_migrations_done = False


class Base(DeclarativeBase):
    pass


def get_db():
    ensure_runtime_migrations()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_runtime_migrations():
    global _runtime_migrations_done
    if _runtime_migrations_done:
        return
    if os.getenv("OPTIPRIME_SKIP_STARTUP_DB", "").lower() in {"1", "true", "yes"}:
        _runtime_migrations_done = True
        return
    run_sqlite_migrations()
    run_postgres_migrations()
    _runtime_migrations_done = True


def init_db():
    from models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_sqlite_migrations()
    run_postgres_migrations()


def run_sqlite_migrations():
    if not DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    migrations = {
        "tasks.team_id": "ALTER TABLE tasks ADD COLUMN team_id INTEGER REFERENCES teams(id)",
        "tasks.parent_task_id": "ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id)",
        "bom_items.team_id": "ALTER TABLE bom_items ADD COLUMN team_id INTEGER REFERENCES teams(id)",
        "bom_items.category": "ALTER TABLE bom_items ADD COLUMN category VARCHAR(120) DEFAULT '' NOT NULL",
        "bom_items.product_number": "ALTER TABLE bom_items ADD COLUMN product_number VARCHAR(120) DEFAULT '' NOT NULL",
        "bom_items.product": "ALTER TABLE bom_items ADD COLUMN product VARCHAR(220) DEFAULT '' NOT NULL",
        "bom_items.vendor": "ALTER TABLE bom_items ADD COLUMN vendor VARCHAR(160) DEFAULT '' NOT NULL",
        "bom_items.sponsored_by": "ALTER TABLE bom_items ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
        "bom_items.finalized": "ALTER TABLE bom_items ADD COLUMN finalized BOOLEAN DEFAULT 0 NOT NULL",
        "bom_versions.category": "ALTER TABLE bom_versions ADD COLUMN category VARCHAR(120) DEFAULT '' NOT NULL",
        "bom_versions.product_number": "ALTER TABLE bom_versions ADD COLUMN product_number VARCHAR(120) DEFAULT '' NOT NULL",
        "bom_versions.product": "ALTER TABLE bom_versions ADD COLUMN product VARCHAR(220) DEFAULT '' NOT NULL",
        "bom_versions.vendor": "ALTER TABLE bom_versions ADD COLUMN vendor VARCHAR(160) DEFAULT '' NOT NULL",
        "bom_versions.sponsored_by": "ALTER TABLE bom_versions ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
        "budget_logs.team_id": "ALTER TABLE budget_logs ADD COLUMN team_id INTEGER REFERENCES teams(id)",
        "budget_logs.sponsored_by": "ALTER TABLE budget_logs ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
        "budget_logs.currency": "ALTER TABLE budget_logs ADD COLUMN currency VARCHAR(3) DEFAULT 'SGD' NOT NULL",
        "budget_logs.quantity": "ALTER TABLE budget_logs ADD COLUMN quantity FLOAT DEFAULT 1 NOT NULL",
        "budget_logs.original_amount": "ALTER TABLE budget_logs ADD COLUMN original_amount FLOAT DEFAULT 0 NOT NULL",
        "budget_logs.exchange_rate_to_sgd": "ALTER TABLE budget_logs ADD COLUMN exchange_rate_to_sgd FLOAT DEFAULT 1 NOT NULL",
        "budget_logs.invoice_id": "ALTER TABLE budget_logs ADD COLUMN invoice_id INTEGER REFERENCES invoices(id)",
        "users.team_id": "ALTER TABLE users ADD COLUMN team_id INTEGER REFERENCES teams(id)",
        "invoices.file_data": "ALTER TABLE invoices ADD COLUMN file_data TEXT DEFAULT '' NOT NULL",
        "invoices.budget_log_id": "ALTER TABLE invoices ADD COLUMN budget_log_id INTEGER REFERENCES budget_logs(id)",
        "invoices.invoice_date": "ALTER TABLE invoices ADD COLUMN invoice_date DATE",
        "invoices.currency": "ALTER TABLE invoices ADD COLUMN currency VARCHAR(3) DEFAULT 'SGD' NOT NULL",
        "invoices.original_amount": "ALTER TABLE invoices ADD COLUMN original_amount FLOAT DEFAULT 0 NOT NULL",
        "invoices.exchange_rate_to_sgd": "ALTER TABLE invoices ADD COLUMN exchange_rate_to_sgd FLOAT DEFAULT 1 NOT NULL",
        "invoices.amount_sgd": "ALTER TABLE invoices ADD COLUMN amount_sgd FLOAT DEFAULT 0 NOT NULL",
        "invoices.vendor": "ALTER TABLE invoices ADD COLUMN vendor VARCHAR(160) DEFAULT '' NOT NULL",
        "invoices.invoice_number": "ALTER TABLE invoices ADD COLUMN invoice_number VARCHAR(120) DEFAULT '' NOT NULL",
        "invoices.sponsored_by": "ALTER TABLE invoices ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
    }
    create_statements = {
        "invoices": """
            CREATE TABLE invoices (
                id INTEGER NOT NULL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                team_id INTEGER REFERENCES teams(id),
                budget_log_id INTEGER REFERENCES budget_logs(id),
                vendor VARCHAR(160) DEFAULT '' NOT NULL,
                invoice_number VARCHAR(120) DEFAULT '' NOT NULL,
                sponsored_by VARCHAR(160) DEFAULT '' NOT NULL,
                description VARCHAR(220) NOT NULL,
                invoice_date DATE,
                currency VARCHAR(3) DEFAULT 'SGD' NOT NULL,
                original_amount FLOAT DEFAULT 0 NOT NULL,
                exchange_rate_to_sgd FLOAT DEFAULT 1 NOT NULL,
                amount_sgd FLOAT DEFAULT 0 NOT NULL,
                original_filename VARCHAR(220) NOT NULL,
                stored_filename VARCHAR(260) NOT NULL,
                file_data TEXT DEFAULT '' NOT NULL,
                uploaded_at DATETIME NOT NULL
            )
        """,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS budget_log_teams (
                    budget_log_id INTEGER NOT NULL REFERENCES budget_logs(id) ON DELETE CASCADE,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    PRIMARY KEY (budget_log_id, team_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS budget_log_references (
                    source_log_id INTEGER NOT NULL REFERENCES budget_logs(id) ON DELETE CASCADE,
                    target_log_id INTEGER NOT NULL REFERENCES budget_logs(id) ON DELETE CASCADE,
                    PRIMARY KEY (source_log_id, target_log_id)
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budget_log_teams_team_id ON budget_log_teams (team_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budget_log_references_target_log_id ON budget_log_references (target_log_id)"))
        for table, statement in create_statements.items():
            if table not in existing_tables:
                connection.execute(text(statement))
        for table_key, statement in migrations.items():
            table = table_key.split(".")[0]
            if table not in existing_tables:
                continue
            columns = {column["name"] for column in inspector.get_columns(table)}
            column_name = table_key.split(".")[1]
            if column_name not in columns:
                connection.execute(text(statement))
        if "budget_logs" in existing_tables:
            connection.execute(text("UPDATE budget_logs SET original_amount = amount WHERE original_amount = 0 AND amount <> 0"))
        if "invoices" in existing_tables:
            connection.execute(text("UPDATE invoices SET vendor = 'Unassigned Vendor' WHERE TRIM(COALESCE(vendor, '')) = ''"))
            connection.execute(text("UPDATE invoices SET invoice_number = 'INV-' || id WHERE TRIM(COALESCE(invoice_number, '')) = ''"))
            connection.execute(text("UPDATE invoices SET team_id = NULL WHERE team_id IS NOT NULL"))
        if "budget_logs" in existing_tables and "invoices" in existing_tables:
            connection.execute(
                text(
                    """
                    UPDATE budget_logs
                    SET invoice_id = (SELECT invoices.id FROM invoices WHERE invoices.budget_log_id = budget_logs.id)
                    WHERE invoice_id IS NULL
                      AND EXISTS (SELECT 1 FROM invoices WHERE invoices.budget_log_id = budget_logs.id)
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO budget_log_teams (budget_log_id, team_id)
                    SELECT budget_logs.id, budget_logs.team_id
                    FROM budget_logs
                    WHERE budget_logs.team_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM budget_log_teams
                          WHERE budget_log_teams.budget_log_id = budget_logs.id
                      )
                    """
                )
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budget_logs_invoice_id ON budget_logs (invoice_id)"))
            connection.execute(
                text(
                    """
                    UPDATE invoices
                    SET sponsored_by = COALESCE(
                        (SELECT budget_logs.sponsored_by
                         FROM budget_logs
                         WHERE budget_logs.invoice_id = invoices.id
                           AND TRIM(COALESCE(budget_logs.sponsored_by, '')) <> ''
                         LIMIT 1),
                        ''
                    )
                    WHERE TRIM(COALESCE(sponsored_by, '')) = ''
                    """
                )
            )


def run_postgres_migrations():
    if not DATABASE_URL.startswith("postgresql"):
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE IF EXISTS tasks
                ALTER COLUMN dependencies TYPE JSONB
                USING COALESCE(dependencies::jsonb, '[]'::jsonb)
                """
            )
        )
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS file_data TEXT DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_items ADD COLUMN IF NOT EXISTS finalized BOOLEAN DEFAULT FALSE NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_items ADD COLUMN IF NOT EXISTS product_number VARCHAR(120) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_items ADD COLUMN IF NOT EXISTS product VARCHAR(220) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_items ADD COLUMN IF NOT EXISTS vendor VARCHAR(160) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_versions ADD COLUMN IF NOT EXISTS product_number VARCHAR(120) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_versions ADD COLUMN IF NOT EXISTS product VARCHAR(220) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS bom_versions ADD COLUMN IF NOT EXISTS vendor VARCHAR(160) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS budget_logs ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'SGD' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS budget_logs ADD COLUMN IF NOT EXISTS quantity DOUBLE PRECISION DEFAULT 1 NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS budget_logs ADD COLUMN IF NOT EXISTS original_amount DOUBLE PRECISION DEFAULT 0 NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS budget_logs ADD COLUMN IF NOT EXISTS exchange_rate_to_sgd DOUBLE PRECISION DEFAULT 1 NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS budget_logs ADD COLUMN IF NOT EXISTS invoice_id INTEGER REFERENCES invoices(id)"))
        connection.execute(text("UPDATE budget_logs SET original_amount = amount WHERE original_amount = 0 AND amount <> 0"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS budget_log_id INTEGER REFERENCES budget_logs(id)"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS invoice_date DATE"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'SGD' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS original_amount DOUBLE PRECISION DEFAULT 0 NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS exchange_rate_to_sgd DOUBLE PRECISION DEFAULT 1 NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS amount_sgd DOUBLE PRECISION DEFAULT 0 NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS vendor VARCHAR(160) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS invoice_number VARCHAR(120) DEFAULT '' NOT NULL"))
        connection.execute(text("ALTER TABLE IF EXISTS invoices ADD COLUMN IF NOT EXISTS sponsored_by VARCHAR(160) DEFAULT '' NOT NULL"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS budget_log_teams (
                    budget_log_id INTEGER NOT NULL REFERENCES budget_logs(id) ON DELETE CASCADE,
                    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    PRIMARY KEY (budget_log_id, team_id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS budget_log_references (
                    source_log_id INTEGER NOT NULL REFERENCES budget_logs(id) ON DELETE CASCADE,
                    target_log_id INTEGER NOT NULL REFERENCES budget_logs(id) ON DELETE CASCADE,
                    PRIMARY KEY (source_log_id, target_log_id)
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budget_log_teams_team_id ON budget_log_teams (team_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budget_log_references_target_log_id ON budget_log_references (target_log_id)"))
        connection.execute(text("UPDATE invoices SET vendor = 'Unassigned Vendor' WHERE BTRIM(COALESCE(vendor, '')) = ''"))
        connection.execute(text("UPDATE invoices SET invoice_number = 'INV-' || id::text WHERE BTRIM(COALESCE(invoice_number, '')) = ''"))
        connection.execute(text("UPDATE invoices SET team_id = NULL WHERE team_id IS NOT NULL"))
        connection.execute(
            text(
                """
                UPDATE budget_logs
                SET invoice_id = invoices.id
                FROM invoices
                WHERE budget_logs.invoice_id IS NULL
                  AND invoices.budget_log_id = budget_logs.id
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_budget_logs_invoice_id ON budget_logs (invoice_id)"))
        connection.execute(
            text(
                """
                INSERT INTO budget_log_teams (budget_log_id, team_id)
                SELECT budget_logs.id, budget_logs.team_id
                FROM budget_logs
                WHERE budget_logs.team_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM budget_log_teams
                      WHERE budget_log_teams.budget_log_id = budget_logs.id
                  )
                ON CONFLICT (budget_log_id, team_id) DO NOTHING
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE invoices
                SET sponsored_by = COALESCE(
                    (SELECT budget_logs.sponsored_by
                     FROM budget_logs
                     WHERE budget_logs.invoice_id = invoices.id
                       AND BTRIM(COALESCE(budget_logs.sponsored_by, '')) <> ''
                     LIMIT 1),
                    ''
                )
                WHERE BTRIM(COALESCE(invoices.sponsored_by, '')) = ''
                """
            )
        )
