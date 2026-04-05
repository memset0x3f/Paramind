from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine


def create_sqlite_engine(database_path):
    engine = create_engine(
        f"sqlite:///{database_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    return engine


def init_db(engine):
    SQLModel.metadata.create_all(engine)
    inspector = inspect(engine)
    event_log_columns = {column["name"] for column in inspector.get_columns("eventlog")}
    if "scope" not in event_log_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE eventlog ADD COLUMN scope VARCHAR DEFAULT 'conversation'"
                )
            )
            connection.execute(
                text(
                    "UPDATE eventlog "
                    "SET scope = CASE WHEN conversation_id IS NULL THEN 'global' ELSE 'conversation' END "
                    "WHERE scope IS NULL OR scope = 'conversation'"
                )
            )


@contextmanager
def session_scope(engine):
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
