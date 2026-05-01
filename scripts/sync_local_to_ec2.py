"""Dump every table from local SQLite to per-table CSV files for transfer
to the EC2 Postgres. The local DB has the full processed dataset (472k
WA messages, 365k resolved, 44k calls, 9k identity nodes) — much faster
to ship than re-run the backfill on EC2 where Periskope's rate limit
keeps killing the run.

Usage:
    python scripts/sync_local_to_ec2.py /tmp/sync_dump
"""
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm_app.db import SessionLocal
from crm_app.models import (
    Binding, Call, Contact, Identity, Issue, Meeting, MeetingAttendee, Note,
    Shop, WhatsAppGroup, WhatsAppGroupEvent, WhatsAppRawMessage,
)


# Tables in FK dependency order — must load shops before contacts, etc.
TABLES = [
    ("shops",                  Shop),
    ("contacts",               Contact),
    ("whatsapp_groups",        WhatsAppGroup),
    ("whatsapp_group_events",  WhatsAppGroupEvent),
    ("meetings",               Meeting),
    ("meeting_attendees",      MeetingAttendee),
    ("calls",                  Call),
    ("whatsapp_raw_messages",  WhatsAppRawMessage),
    ("identities",             Identity),
    ("bindings",               Binding),
    ("issues",                 Issue),
    ("notes",                  Note),
]


def csv_value(v):
    """Convert SQLAlchemy column value into a Postgres-COPY-friendly form."""
    if v is None:
        return r"\N"
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, datetime):
        # Naive UTC — Postgres TIMESTAMP WITHOUT TIME ZONE accepts ISO directly
        return v.isoformat(sep=" ")
    return str(v)


def dump_table(out_dir: str, table_name: str, model) -> int:
    """Write `table_name`.csv with all columns in declaration order.
    Use Postgres COPY's default format (tab-separated, \\N for NULL)."""
    cols = [c.name for c in model.__table__.columns]
    path = os.path.join(out_dir, f"{table_name}.csv")
    db = SessionLocal()
    n = 0
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="\t", quoting=csv.QUOTE_NONE,
                           escapechar="\\")
            for row in db.query(model).yield_per(2000):
                values = [csv_value(getattr(row, c)) for c in cols]
                # Tabs and newlines inside a value would break the parse.
                # We're conservative — replace them.
                values = [v.replace("\t", "    ").replace("\n", " ").replace("\r", " ") for v in values]
                w.writerow(values)
                n += 1
        return n
    finally:
        db.close()


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/sync_dump"
    os.makedirs(out_dir, exist_ok=True)

    # Write a manifest so the import side knows column order.
    manifest = []
    print(f"=== dumping to {out_dir} ===")
    for name, model in TABLES:
        cols = [c.name for c in model.__table__.columns]
        n = dump_table(out_dir, name, model)
        manifest.append((name, cols, n))
        print(f"  {name:28} {n:>7} rows  ({len(cols)} cols)")

    # Manifest is consumed by the import script.
    with open(os.path.join(out_dir, "_manifest.tsv"), "w", encoding="utf-8") as f:
        for name, cols, n in manifest:
            f.write(f"{name}\t{n}\t{','.join(cols)}\n")
    print(f"\nManifest: {out_dir}/_manifest.tsv")


if __name__ == "__main__":
    main()
