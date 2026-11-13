"""Refresh the HTML guide's schema snapshot using the actual SQLite migrations."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from storage import MIGRATIONS, apply_migrations, connect_database


def schema_snapshot():
    with closing(connect_database(":memory:")) as db:
        apply_migrations(db)
        tables = []
        names = db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for table in names:
            name = table["name"]
            columns = [dict(row) for row in db.execute(f'PRAGMA table_info("{name}")')]
            foreign_keys = [dict(row) for row in db.execute(f'PRAGMA foreign_key_list("{name}")')]
            indexes = []
            for index in db.execute(f'PRAGMA index_list("{name}")'):
                sql = db.execute("SELECT sql FROM sqlite_master WHERE name = ?", (index["name"],)).fetchone()
                indexes.append({
                    **dict(index),
                    "columns": [row["name"] for row in db.execute(f'PRAGMA index_info("{index["name"]}")')],
                    "sql": sql[0] if sql else None,
                })
            triggers = [dict(row) for row in db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? ORDER BY name", (name,)
            )]
            tables.append({"name": name, "sql": table["sql"], "columns": columns,
                           "foreignKeys": foreign_keys, "indexes": indexes, "triggers": triggers})
        history = [dict(row) for row in db.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")]
    migration_sql = "\n\n".join(path.read_text() for path in sorted(MIGRATIONS.glob("*.sql")))
    return {"tables": tables, "migrations": history, "sql": migration_sql,
            "schemaHash": hashlib.sha256(migration_sql.encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = BACKEND / "guides" / "database-guide.html"
    source = path.read_text()
    snapshot = json.dumps(schema_snapshot(), ensure_ascii=False, separators=(",", ":"))
    snapshot = snapshot.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    updated, count = re.subn(
        r'(<script id="schema-data" type="application/json">).*?(</script>)',
        lambda match: match[1] + snapshot + match[2], source, count=1, flags=re.DOTALL,
    )
    if count != 1:
        raise SystemExit("Expected one schema-data block in the guide.")
    if args.check and source != updated:
        raise SystemExit("Guide schema snapshot is stale; run this command without --check.")
    if not args.check:
        path.write_text(updated)
    print("Guide schema snapshot matches SQLite migrations." if args.check else "Updated guide from SQLite migrations.")


if __name__ == "__main__":
    main()
