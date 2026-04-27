"""Shared helpers used across loaders and webhook receivers."""
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import Contact

_PHONE_DIGITS = re.compile(r"\D+")


def norm_phone(p: Optional[str]) -> Optional[str]:
    """Canonical phone digits for cross-source matching.

    1. Strip non-digits.
    2. If the result is 11–13 digits (likely an Indian mobile with country
       code), keep just the last 10. Indian merchant phones arrive as a
       mix of "9999999999" and "+919999999999" / "919999999999" across
       sources (master CSV, finance sheet, WhatsApp, FreJun); collapsing
       to the last-10 makes them match.
    3. Anything 10 digits or under stays as-is.
    4. Anything longer than 13 digits stays as-is — likely a true
       international number where the leading digits matter.

    Returns None for empty input.
    """
    if not p:
        return None
    digits = _PHONE_DIGITS.sub("", p)
    if not digits:
        return None
    if 11 <= len(digits) <= 13:
        return digits[-10:]
    return digits


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
