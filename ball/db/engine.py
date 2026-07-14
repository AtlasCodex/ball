"""SQLAlchemy 引擎与会话管理。"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ball.config import get

_url: str = get("database.url", "sqlite:///data/ball.db")

if _url.startswith("sqlite:///"):
    db_path = _url[len("sqlite:///"):]
    parent = Path(db_path).parent
    if str(parent) not in (".", ""):
        parent.mkdir(parents=True, exist_ok=True)
    else:
        Path("data").mkdir(parents=True, exist_ok=True)

engine = create_engine(_url, echo=False, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


@contextmanager
def session_scope() -> Session:
    """事务性会话上下文：成功提交，异常回滚。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
