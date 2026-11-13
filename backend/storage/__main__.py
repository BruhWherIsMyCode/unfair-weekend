"""Usage from backend/: python3 -m storage --database .cache/checkin.sqlite3."""

import argparse
from contextlib import closing
from pathlib import Path

from . import apply_migrations, connect_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply checked-in SQLite migrations.")
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_database(args.database)) as connection:
        applied = apply_migrations(connection)
        count = connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    print(f"Applied {len(applied)} migration(s); schema version {count}.")


if __name__ == "__main__":
    main()
