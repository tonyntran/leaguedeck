from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.crypto import decrypt_value, encrypt_value
from app.db import get_db
from app.models import AppSetting, Secret

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


class SleeperSettings(BaseModel):
    username: str
    league_ids: list[str]


class EspnSettings(BaseModel):
    espn_s2: str | None = None
    swid: str | None = None
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


def _set_secret(db: Session, key: str, value: str) -> None:
    row = db.query(Secret).filter(Secret.key == key).first()
    encrypted = encrypt_value(value)
    if row is None:
        db.add(Secret(key=key, encrypted_value=encrypted))
    else:
        row.encrypted_value = encrypted
    db.commit()


def _secret_configured(db: Session, key: str) -> bool:
    return db.query(Secret).filter(Secret.key == key).first() is not None


@router.get("/sleeper")
def get_sleeper_settings(db: Session = Depends(get_db)):
    username = _get_setting(db, "sleeper_username") or ""
    league_ids_raw = _get_setting(db, "sleeper_league_ids") or ""
    return {
        "username": username,
        "league_ids": [x.strip() for x in league_ids_raw.split(",") if x.strip()],
    }


@router.put("/sleeper")
def set_sleeper_settings(payload: SleeperSettings, db: Session = Depends(get_db)):
    _set_setting(db, "sleeper_username", payload.username.strip())
    _set_setting(
        db,
        "sleeper_league_ids",
        ",".join(x.strip() for x in payload.league_ids if x.strip()),
    )
    return {"ok": True}


@router.get("/espn")
def get_espn_settings(db: Session = Depends(get_db)):
    league_ids_raw = _get_setting(db, "espn_league_ids") or ""
    return {
        "league_ids": [x.strip() for x in league_ids_raw.split(",") if x.strip()],
        "espn_s2_configured": _secret_configured(db, "espn_s2"),
        "swid_configured": _secret_configured(db, "espn_swid"),
    }


@router.put("/espn")
def set_espn_settings(payload: EspnSettings, db: Session = Depends(get_db)):
    # espn_s2/swid are optional: omitted or blank means "leave unchanged".
    # A naive always-overwrite here would let a league-IDs-only edit (the
    # common case, since GET never echoes the real cookie values back into
    # the form) silently wipe already-stored credentials.
    if payload.espn_s2 and payload.espn_s2.strip():
        _set_secret(db, "espn_s2", payload.espn_s2.strip())
    if payload.swid and payload.swid.strip():
        _set_secret(db, "espn_swid", payload.swid.strip())
    _set_setting(
        db,
        "espn_league_ids",
        ",".join(x.strip() for x in payload.league_ids if x.strip()),
    )
    return {"ok": True}
