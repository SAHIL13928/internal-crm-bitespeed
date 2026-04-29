"""Identity graph — connect typed nodes (shop_url, phone, email, meeting_link,
group_name) via observed co-occurrences in real events.

A connected component is one merchant. Adding a single binding can resolve
many previously-orphan records, which is why every webhook handler should
call `add_binding` whenever it observes a real co-occurrence (e.g. a WA
message whose phone is in our static directory ⇒ phone↔shop_url binding,
group_name↔shop_url binding).

Resolution is BFS up to depth 3 from the query node. We cap depth so a
single accidental edge in a dense subgraph cannot rewrite resolution for
distant nodes.

Conflict semantics: if a connected component reachable within depth 3
contains MULTIPLE shop_url nodes, `resolve_shop_url_for` returns the marker
'conflict' rather than guessing. The /admin/conflicts endpoint surfaces
these for human review.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Iterable, Optional, Tuple

from sqlalchemy.orm import Session

from .models import Binding, Identity
from .time_utils import utcnow_naive

VALID_KINDS = {"shop_url", "phone", "email", "meeting_link", "group_name"}
CONFLICT = "conflict"  # sentinel returned by resolve_shop_url_for on conflict
DEFAULT_DEPTH = 3


# ── value normalization ───────────────────────────────────────────────────
# Identity.value is the canonical form per kind. Normalize at the API
# boundary so {kind, value} truly identifies a node — otherwise "+91..." vs
# "91..." vs "(+91) ..." would each be distinct nodes.
from .utils import norm_phone as _norm_phone


def normalize(kind: str, value: Optional[str]) -> Optional[str]:
    """Canonical form per kind. Phone normalization delegates to
    `utils.norm_phone` so identity graph and `phone_to_shop` agree
    on what counts as the same phone (Indian numbers with/without
    country code collapse to the last-10)."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if kind == "phone":
        return _norm_phone(v)
    if kind == "email":
        return v.lower()
    if kind == "shop_url":
        return v.lower()
    return v


# ── core operations ───────────────────────────────────────────────────────
def get_or_create_identity(db: Session, kind: str, value: str) -> Identity:
    if kind not in VALID_KINDS:
        raise ValueError(f"unsupported identity kind: {kind!r}")
    norm = normalize(kind, value)
    if norm is None:
        raise ValueError(f"empty/invalid value for kind={kind!r}")
    row = db.query(Identity).filter_by(kind=kind, value=norm).first()
    if row is None:
        row = Identity(kind=kind, value=norm)
        db.add(row)
        db.flush()
    return row


def add_binding(
    db: Session,
    a_kind: str,
    a_val: str,
    b_kind: str,
    b_val: str,
    source: str,
    confidence: float = 1.0,
    evidence_table: Optional[str] = None,
    evidence_id: Optional[str] = None,
    observed_at: Optional[datetime] = None,
) -> Optional[Binding]:
    """Idempotent. Creates identities if missing. Skips self-edges and
    duplicates by natural key (a, b, source, evidence_id).

    `evidence_id` is part of the unique key. Callers should always supply
    one (a row id, a hash, or a deterministic stand-in) so SQLite's
    "NULLs are distinct" rule doesn't let duplicate edges through."""
    a = get_or_create_identity(db, a_kind, a_val)
    b = get_or_create_identity(db, b_kind, b_val)

    if a.id == b.id:
        return None  # self-edge — nothing to record

    # Enforce undirected ordering: a_id < b_id.
    a_id, b_id = (a.id, b.id) if a.id < b.id else (b.id, a.id)

    if evidence_id is None:
        # Deterministic stand-in keeps the unique constraint useful even
        # when callers don't have a stable evidence row id (e.g. seeding
        # from the static directory). Two seeds with identical (a,b,source)
        # collapse to one binding.
        evidence_id = f"{evidence_table or 'unknown'}:{a_id}:{b_id}"

    existing = (
        db.query(Binding)
        .filter_by(
            identity_a_id=a_id,
            identity_b_id=b_id,
            source=source,
            evidence_id=evidence_id,
        )
        .first()
    )
    if existing:
        return existing

    binding = Binding(
        identity_a_id=a_id,
        identity_b_id=b_id,
        source=source,
        confidence=confidence,
        observed_at=observed_at or utcnow_naive(),
        evidence_table=evidence_table,
        evidence_id=evidence_id,
    )
    db.add(binding)
    db.flush()
    return binding


def _component_neighbors(db: Session, ids: Iterable[int]) -> list[Tuple[int, int, float]]:
    """Return all edges (a_id, b_id, confidence) touching any id in `ids`."""
    ids = list(ids)
    if not ids:
        return []
    rows = (
        db.query(Binding.identity_a_id, Binding.identity_b_id, Binding.confidence)
        .filter((Binding.identity_a_id.in_(ids)) | (Binding.identity_b_id.in_(ids)))
        .all()
    )
    return [(a, b, c) for a, b, c in rows]


def resolve_shop_url_for(
    db: Session, kind: str, value: str, max_depth: int = DEFAULT_DEPTH
) -> Optional[str]:
    """BFS the identity graph up to `max_depth` hops from (kind, value).
    Among the shop_url nodes reached, return the value with the highest
    accumulated confidence. If multiple distinct shop_urls are reachable,
    return the sentinel CONFLICT. Returns None if the node doesn't exist
    or no shop_url is reachable.

    Why bounded depth: real co-occurrence chains shouldn't need >3 hops,
    and capping prevents one bad edge in a dense subgraph from poisoning
    resolution for distant nodes. The /admin/conflicts endpoint surfaces
    cases where two shop_urls genuinely sit in the same component."""
    if kind not in VALID_KINDS:
        return None
    norm = normalize(kind, value)
    if norm is None:
        return None
    start = db.query(Identity).filter_by(kind=kind, value=norm).first()
    if start is None:
        return None

    if kind == "shop_url":
        return start.value

    # BFS. Track best (highest-confidence path) score per shop_url.
    visited: dict[int, float] = {start.id: 1.0}
    frontier: deque[tuple[int, int, float]] = deque([(start.id, 0, 1.0)])
    shop_scores: dict[str, float] = {}  # shop_url value -> best path confidence

    while frontier:
        node_id, depth, score = frontier.popleft()
        if depth >= max_depth:
            continue
        edges = _component_neighbors(db, [node_id])
        for a, b, conf in edges:
            other = b if a == node_id else a
            new_score = score * (conf if conf is not None else 1.0)
            if other in visited and visited[other] >= new_score:
                continue
            visited[other] = new_score
            frontier.append((other, depth + 1, new_score))

    if not visited:
        return None

    # Look up identities reached and pick out shop_urls.
    reached_ids = list(visited.keys())
    shop_rows = (
        db.query(Identity.id, Identity.value)
        .filter(Identity.kind == "shop_url", Identity.id.in_(reached_ids))
        .all()
    )
    for ident_id, val in shop_rows:
        s = visited[ident_id]
        if val not in shop_scores or shop_scores[val] < s:
            shop_scores[val] = s

    if not shop_scores:
        return None
    if len(shop_scores) == 1:
        return next(iter(shop_scores.keys()))

    # Multiple distinct shop_urls reachable → conflict.
    return CONFLICT


def find_conflicts(db: Session, limit: int = 100) -> list[dict]:
    """Walk every non-shop identity and find ones whose component contains
    >1 shop_url within DEFAULT_DEPTH. Used by /admin/conflicts.

    Note: O(N · BFS). Fine for the current 1.6k-shop scale; revisit when
    we have ~100k identities."""
    out = []
    rows = (
        db.query(Identity.kind, Identity.value)
        .filter(Identity.kind != "shop_url")
        .all()
    )
    seen_components: set[frozenset] = set()
    for kind, val in rows:
        result = resolve_shop_url_for(db, kind, val)
        if result == CONFLICT:
            # Compute the actual conflicting shop_urls for the report
            urls = _shop_urls_in_component(db, kind, val)
            key = frozenset(urls)
            if key in seen_components:
                continue
            seen_components.add(key)
            out.append({"kind": kind, "value": val, "shop_urls": sorted(urls)})
            if len(out) >= limit:
                break
    return out


def _shop_urls_in_component(db: Session, kind: str, value: str) -> set[str]:
    """Return the set of shop_url values reachable from (kind, value) within
    DEFAULT_DEPTH. Helper for find_conflicts."""
    norm = normalize(kind, value)
    if norm is None:
        return set()
    start = db.query(Identity).filter_by(kind=kind, value=norm).first()
    if start is None:
        return set()
    visited: set[int] = {start.id}
    frontier: deque[tuple[int, int]] = deque([(start.id, 0)])
    while frontier:
        node_id, depth = frontier.popleft()
        if depth >= DEFAULT_DEPTH:
            continue
        edges = _component_neighbors(db, [node_id])
        for a, b, _conf in edges:
            other = b if a == node_id else a
            if other in visited:
                continue
            visited.add(other)
            frontier.append((other, depth + 1))
    if not visited:
        return set()
    rows = (
        db.query(Identity.value)
        .filter(Identity.kind == "shop_url", Identity.id.in_(visited))
        .all()
    )
    return {v for (v,) in rows}
