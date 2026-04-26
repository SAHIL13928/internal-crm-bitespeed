"""Shared helpers used across loaders and webhook receivers."""
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import Contact

_PHONE_DIGITS = re.compile(r"\D+")


def norm_phone(p: Optional[str]) -> Optional[str]:
    """Strip everything but digits. None/empty -> None."""
    if not p:
        return None
    digits = _PHONE_DIGITS.sub("", p)
    return digits or None


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert tz-aware -> naive UTC. Naive in -> naive out. Matches the rest of the codebase."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def build_phone_to_shop(db: Session) -> dict:
    """Map digits-only phone -> shop_url, built from the contacts table.
    First match wins on collision."""
    out: dict = {}
    rows = (
        db.query(Contact.phone, Contact.shop_url)
        .filter(Contact.phone.isnot(None), Contact.shop_url.isnot(None))
        .all()
    )
    for phone, shop in rows:
        n = norm_phone(phone)
        if n and n not in out:
            out[n] = shop
    return out
