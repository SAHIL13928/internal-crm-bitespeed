"""Per-event shop-url resolution used by both webhook handlers.

Strategy: try the static directory first (cheap, exact match against
contacts.phone / whatsapp_groups.group_name), then fall back to the identity
graph (BFS depth ≤3). Whenever resolution succeeds, opportunistically
add bindings for any newly-observed (kind, value)↔shop_url pair so the
graph keeps growing.

This module is pure logic — handlers pass in the SQLAlchemy session and
take responsibility for transactions / commits.
"""
from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy.orm import Session

from . import identity as _identity
from .models import WhatsAppGroup
from .utils import build_phone_to_shop, norm_phone


def _static_lookup_phone(db: Session, phone: Optional[str]) -> Optional[str]:
    """Direct match against contacts.phone. Returns shop_url or None."""
    if not phone:
        return None
    p2s = build_phone_to_shop(db)
    return p2s.get(norm_phone(phone))


def _static_lookup_group(db: Session, group_name: Optional[str]) -> Optional[str]:
    """Direct match against whatsapp_groups.group_name. Returns shop_url
    only when there's exactly one mapped match — ambiguous group names
    are not resolved here (the identity graph can still resolve them
    later via phone co-occurrence)."""
    if not group_name:
        return None
    rows = (
        db.query(WhatsAppGroup.shop_url)
        .filter(WhatsAppGroup.group_name == group_name)
        .filter(WhatsAppGroup.shop_url.isnot(None))
        .distinct()
        .all()
    )
    urls = {u for (u,) in rows}
    if len(urls) == 1:
        return next(iter(urls))
    return None


def resolve_whatsapp_message(
    db: Session,
    sender_phone: Optional[str],
    group_name: Optional[str],
    evidence_table: str = "whatsapp_raw_messages",
    evidence_id: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """Returns (shop_url_or_marker, method).

    `shop_url_or_marker` is a real shop_url, or 'conflict', or None.
    `method` is one of:
      - 'static_directory_phone'
      - 'static_directory_group'
      - 'static_directory_both'   (phone+group agree)
      - 'static_directory_conflict'  (phone and group resolve to different shops)
      - 'identity_graph'
      - 'identity_graph_conflict'
      - 'unresolved'              (never returned with a shop_url)

    On any successful resolution we grow the graph by adding bindings
    so future events can resolve via the graph too."""
    by_phone = _static_lookup_phone(db, sender_phone)
    by_group = _static_lookup_group(db, group_name)

    if by_phone and by_group:
        if by_phone == by_group:
            _grow_graph(db, sender_phone, group_name, by_phone, evidence_table, evidence_id)
            return by_phone, "static_directory_both"
        # phone says one shop, group says another. This is a real conflict
        # in the static directory — surface, don't guess.
        return _identity.CONFLICT, "static_directory_conflict"

    if by_phone:
        _grow_graph(db, sender_phone, group_name, by_phone, evidence_table, evidence_id)
        return by_phone, "static_directory_phone"

    if by_group:
        _grow_graph(db, sender_phone, group_name, by_group, evidence_table, evidence_id)
        return by_group, "static_directory_group"

    # Static directory failed → try the identity graph.
    candidates = []
    if sender_phone:
        candidates.append(("phone", sender_phone))
    if group_name:
        candidates.append(("group_name", group_name))

    found_url: Optional[str] = None
    saw_conflict = False
    for kind, value in candidates:
        result = _identity.resolve_shop_url_for(db, kind, value)
        if result == _identity.CONFLICT:
            saw_conflict = True
            continue
        if result:
            if found_url and found_url != result:
                saw_conflict = True
                break
            found_url = result

    if found_url and not saw_conflict:
        _grow_graph(db, sender_phone, group_name, found_url, evidence_table, evidence_id)
        return found_url, "identity_graph"
    if saw_conflict:
        return _identity.CONFLICT, "identity_graph_conflict"

    return None, "unresolved"


def resolve_call(
    db: Session,
    counterparty_phone: Optional[str],
    evidence_table: str = "calls",
    evidence_id: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """FreJun calls only have a counterparty phone. Same pattern as WA."""
    by_phone = _static_lookup_phone(db, counterparty_phone)
    if by_phone:
        _grow_graph(db, counterparty_phone, None, by_phone, evidence_table, evidence_id)
        return by_phone, "static_directory_phone"

    if counterparty_phone:
        result = _identity.resolve_shop_url_for(db, "phone", counterparty_phone)
        if result == _identity.CONFLICT:
            return _identity.CONFLICT, "identity_graph_conflict"
        if result:
            _grow_graph(db, counterparty_phone, None, result, evidence_table, evidence_id)
            return result, "identity_graph"

    return None, "unresolved"


def _grow_graph(
    db: Session,
    sender_phone: Optional[str],
    group_name: Optional[str],
    shop_url: str,
    evidence_table: str,
    evidence_id: Optional[str],
):
    """Record new (phone↔shop_url) and (group_name↔shop_url) edges so the
    next event can resolve via the graph. Idempotent — add_binding skips
    duplicates by (a, b, source, evidence_id).

    `source` is the evidence_table by default — that lets /admin/conflicts
    show which feed introduced an edge."""
    src = "whatsapp" if evidence_table == "whatsapp_raw_messages" else (
        "frejun" if evidence_table == "calls" else evidence_table
    )
    try:
        if sender_phone:
            _identity.add_binding(
                db,
                "phone", sender_phone,
                "shop_url", shop_url,
                source=src,
                confidence=0.9,  # observed co-occurrence — high but not 1.0 like static directory
                evidence_table=evidence_table,
                evidence_id=evidence_id,
            )
        if group_name:
            _identity.add_binding(
                db,
                "group_name", group_name,
                "shop_url", shop_url,
                source=src,
                confidence=0.9,
                evidence_table=evidence_table,
                evidence_id=evidence_id,
            )
    except ValueError:
        # normalize() rejected (empty value) — fine, just skip.
        pass
