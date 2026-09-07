from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class League(Base):
    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True)
    platform = Column(String, nullable=False)
    platform_league_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    season = Column(String, nullable=False)

    teams = relationship("Team", back_populates="league", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    platform_team_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    is_mine = Column(Boolean, default=False)
    roster_json = Column(Text, nullable=False, default="[]")
    points_for = Column(Float, default=0.0)
    opponent_name = Column(String, nullable=True)
    opponent_points = Column(Float, nullable=True)
    week = Column(Integer, nullable=True)

    league = relationship("League", back_populates="teams")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True)
    platform = Column(String, nullable=False)
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    success = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)


class Secret(Base):
    """Encrypted platform credentials (ESPN cookies, Yahoo tokens). Unused until
    the ESPN/Yahoo plans, but created now so this table exists from day one."""

    __tablename__ = "secrets"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    encrypted_value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text, nullable=False)
