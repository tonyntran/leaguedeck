from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters import yahoo
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


class YahooSettings(BaseModel):
    client_id: str
    client_secret: str | None = None
    league_ids: list[str]


class YahooAuthorizeCode(BaseModel):
    code: str


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


def _get_secret(db: Session, key: str) -> str | None:
    row = db.query(Secret).filter(Secret.key == key).first()
    return row.encrypted_value if row else None


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


@router.get("/yahoo")
def get_yahoo_settings(db: Session = Depends(get_db)):
    league_ids_raw = _get_setting(db, "yahoo_league_ids") or ""
    return {
        "client_id": _get_setting(db, "yahoo_client_id") or "",
        "league_ids": [x.strip() for x in league_ids_raw.split(",") if x.strip()],
        "client_secret_configured": _secret_configured(db, "yahoo_client_secret"),
        "authorized": _secret_configured(db, "yahoo_access_token"),
    }


@router.put("/yahoo")
def set_yahoo_settings(payload: YahooSettings, db: Session = Depends(get_db)):
    # client_id is not secret -- it's an identifier, the same category as
    # Sleeper's username -- so it always overwrites and is echoed by GET.
    _set_setting(db, "yahoo_client_id", payload.client_id.strip())
    if payload.client_secret and payload.client_secret.strip():
        _set_secret(db, "yahoo_client_secret", payload.client_secret.strip())
    _set_setting(
        db,
        "yahoo_league_ids",
        ",".join(x.strip() for x in payload.league_ids if x.strip()),
    )
    return {"ok": True}


@router.get("/yahoo/authorize-url")
def get_yahoo_authorize_url(db: Session = Depends(get_db)):
    client_id = _get_setting(db, "yahoo_client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="Save a Yahoo client ID first")
    return {"url": yahoo.get_authorize_url(client_id)}


@router.post("/yahoo/authorize")
def authorize_yahoo(payload: YahooAuthorizeCode, db: Session = Depends(get_db)):
    client_id = _get_setting(db, "yahoo_client_id")
    client_secret_encrypted = _get_secret(db, "yahoo_client_secret")
    if not client_id or not client_secret_encrypted:
        raise HTTPException(status_code=400, detail="Save Yahoo credentials first")
    client_secret = decrypt_value(client_secret_encrypted)

    try:
        tokens = yahoo.exchange_code_for_tokens(client_id, client_secret, payload.code)
    except Exception:
        # Never echo Yahoo's raw error body or the pasted code back to the
        # client -- a generic message is enough to act on.
        raise HTTPException(status_code=400, detail="Could not verify that code with Yahoo")

    _set_secret(db, "yahoo_access_token", tokens["access_token"])
    _set_secret(db, "yahoo_refresh_token", tokens["refresh_token"])
    if tokens.get("yahoo_guid"):
        _set_setting(db, "yahoo_guid", tokens["yahoo_guid"])
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
    _set_setting(db, "yahoo_token_expires_at", expires_at.isoformat())
    return {"ok": True}
