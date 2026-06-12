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
        "bom_items.sponsored_by": "ALTER TABLE bom_items ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
        "bom_items.finalized": "ALTER TABLE bom_items ADD COLUMN finalized BOOLEAN DEFAULT 0 NOT NULL",
        "bom_versions.category": "ALTER TABLE bom_versions ADD COLUMN category VARCHAR(120) DEFAULT '' NOT NULL",
        "bom_versions.sponsored_by": "ALTER TABLE bom_versions ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
        "budget_logs.team_id": "ALTER TABLE budget_logs ADD COLUMN team_id INTEGER REFERENCES teams(id)",
        "budget_logs.sponsored_by": "ALTER TABLE budget_logs ADD COLUMN sponsored_by VARCHAR(160) DEFAULT '' NOT NULL",
        "users.team_id": "ALTER TABLE users ADD COLUMN team_id INTEGER REFERENCES teams(id)",
        "invoices.file_data": "ALTER TABLE invoices ADD COLUMN file_data TEXT DEFAULT '' NOT NULL",
    }
    create_statements = {
        "invoices": """
            CREATE TABLE invoices (
                id INTEGER NOT NULL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                team_id INTEGER REFERENCES teams(id),
                description VARCHAR(220) NOT NULL,
                original_filename VARCHAR(220) NOT NULL,
                stored_filename VARCHAR(260) NOT NULL,
                file_data TEXT DEFAULT '' NOT NULL,
                uploaded_at DATETIME NOT NULL
            )
        """,
    }
    with engine.begin() as connection:
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
