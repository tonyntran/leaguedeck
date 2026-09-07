from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

Base = declarative_base()


@lru_cache
def get_engine():
    return create_engine(
        get_settings().database_url,
        connect_args={"check_same_thread": False},
    )


def get_sessionmaker():
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db():
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=get_engine())
