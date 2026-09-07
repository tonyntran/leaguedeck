from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import AppSetting

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


class SleeperSettings(BaseModel):
    username: str
    league_ids: list[str]


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _get_setting(db: Session, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


@router.get("/sleeper")
def get_sleeper_settings(db: Session = Depends(get_db)):
    username = _get_setting(db, "sleeper_username") or ""
    league_ids_raw = _get_setting(db, "sleeper_league_ids") or ""
    return {
        "username": username,
        "league_ids": [x for x in league_ids_raw.split(",") if x],
    }


@router.put("/sleeper")
def set_sleeper_settings(payload: SleeperSettings, db: Session = Depends(get_db)):
    _set_setting(db, "sleeper_username", payload.username)
    _set_setting(db, "sleeper_league_ids", ",".join(payload.league_ids))
    return {"ok": True}
