"""One-shot ETL runner. Re-runnable safely (upserts).

Usage:
    python run_etl.py             # all stages
    python run_etl.py shops       # just shops
    python run_etl.py fireflies   # just meetings
    python run_etl.py frejun      # just calls (needs FREJUN_API_KEY or dump)
"""
import sys

from etl.load_fireflies import load_fireflies
from etl.load_shops import load_shops


def run_frejun():
    # Defer import — only required if user actually has Frejun creds set up
    try:
        from etl import load_frejun as f
    except ImportError as e:
        print(f"Frejun loader unavailable: {e}")
        return
    import os
    if len(sys.argv) > 2:
        f.load_from_file(sys.argv[2])
    elif os.environ.get("FREJUN_DUMP_PATH"):
        f.load_from_file(os.environ["FREJUN_DUMP_PATH"])
    elif os.environ.get("FREJUN_API_KEY"):
        f.load_from_api()
    else:
        print("Skipping Frejun: set FREJUN_API_KEY or FREJUN_DUMP_PATH "
              "(or pass a dump path: `python run_etl.py frejun <path>`)")


STAGES = {
    "shops": load_shops,
    "fireflies": load_fireflies,
    "frejun": run_frejun,
}


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg != "all" and arg not in STAGES:
        print(f"Unknown stage '{arg}'. Choose from: all, {', '.join(STAGES)}")
        sys.exit(1)
    stages = list(STAGES.keys()) if arg == "all" else [arg]
    for s in stages:
        print(f"\n---- {s.upper()} ----")
        STAGES[s]()


if __name__ == "__main__":
    main()
