"""Workflow-scoped access to the selected AP master database."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine

from ap_database.master_models import (
    InvoiceMaster,
    SapGRNMaster,
    SapPOMaster,
    SapPostedInvoiceMaster,
)


REQUIRED_WORKFLOW_MASTER_TABLES = (
    InvoiceMaster.__tablename__,
    SapPOMaster.__tablename__,
    SapGRNMaster.__tablename__,
    SapPostedInvoiceMaster.__tablename__,
)


class MasterDatabaseConfigurationError(RuntimeError):
    """Raised when the selected workflow master database is unusable."""


class WorkflowMasterRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._verified = False

    @property
    def database_identity(self) -> str:
        return self.engine.url.render_as_string(hide_password=True)

    def verify_required_tables(self) -> None:
        if self._verified:
            return
        self._verify_sqlite_file_exists()
        inspector = inspect(self.engine)
        missing = [
            table_name
            for table_name in REQUIRED_WORKFLOW_MASTER_TABLES
            if not inspector.has_table(
                table_name,
                schema=(
                    "master"
                    if self.engine.dialect.name == "postgresql"
                    else None
                ),
            )
        ]
        if missing:
            raise MasterDatabaseConfigurationError(
                "AP master database preflight failed for "
                f"{self.database_identity}. Missing required table(s): "
                f"{', '.join(missing)}."
            )
        self._verified = True

    def connect(self) -> Connection:
        self.verify_required_tables()
        return self.engine.connect()

    def _verify_sqlite_file_exists(self) -> None:
        if self.engine.url.get_backend_name() != "sqlite":
            return
        database = self.engine.url.database
        if not database or database == ":memory:":
            return
        path = Path(database).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise MasterDatabaseConfigurationError(
                "AP master database preflight failed for "
                f"{path.resolve()}. SQLite database file does not exist; "
                "refusing to create an empty database."
            )
