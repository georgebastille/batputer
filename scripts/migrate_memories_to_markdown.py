"""One-time migration: copy SQLite `memories` rows into the markdown memory log.

The rows are appended to `log.md` (profile rows flagged); the next compile run
folds them into the wiki. Safe to run before the `memories` table is dropped.

    python scripts/migrate_memories_to_markdown.py \
        [--db batputer.db] [--vault /path/to/vault]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.markdown_memory import MarkdownMemory  # noqa: E402

DEFAULT_VAULT_PATH = "/Users/richie/Documents/BatCloudLibrary"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("BATPUTER_DB_PATH", "batputer.db"))
    parser.add_argument("--vault", default=os.getenv("BATPUTER_VAULT_PATH", DEFAULT_VAULT_PATH))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        rows = conn.execute(
            "SELECT category, content FROM memories ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        print("No `memories` table found — nothing to migrate.")
        return
    finally:
        conn.close()

    memory = MarkdownMemory(args.vault)
    for category, content in rows:
        memory.append_raw(content, profile=(category == "profile"))

    print(f"Migrated {len(rows)} memory row(s) into {memory.log_path}")


if __name__ == "__main__":
    main()
