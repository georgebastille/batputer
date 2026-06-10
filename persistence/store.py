import json
import sqlite3


class ConversationStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                chat_id      INTEGER NOT NULL,
                seq          INTEGER NOT NULL,
                role         TEXT NOT NULL,
                content      TEXT,
                tool_calls   TEXT,
                tool_call_id TEXT,
                PRIMARY KEY (chat_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_conv_chat
                ON conversations (chat_id);
            CREATE TABLE IF NOT EXISTS seen_emails (
                email_id TEXT PRIMARY KEY,
                seen_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                category   TEXT NOT NULL DEFAULT 'general',
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        self._migrate_food_notes()
        self._conn.commit()

    def _migrate_food_notes(self) -> None:
        exists = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='food_notes'"
        ).fetchone()
        if not exists:
            return
        self._conn.execute(
            "INSERT INTO memories (chat_id, category, content, created_at) "
            "SELECT chat_id, 'general', note, created_at FROM food_notes"
        )
        self._conn.execute("DROP TABLE food_notes")

    def load(self, chat_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id FROM conversations "
            "WHERE chat_id=? ORDER BY seq",
            (chat_id,),
        ).fetchall()
        messages = []
        for row in rows:
            msg: dict = {"role": row["role"]}
            if row["content"] is not None:
                msg["content"] = row["content"]
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            messages.append(msg)
        return messages

    def save_message(self, chat_id: int, message: dict) -> None:
        seq = self._next_seq(chat_id)
        tool_calls = message.get("tool_calls")
        self._conn.execute(
            "INSERT INTO conversations (chat_id, seq, role, content, tool_calls, tool_call_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                seq,
                message["role"],
                message.get("content"),
                json.dumps(tool_calls) if tool_calls else None,
                message.get("tool_call_id"),
            ),
        )
        self._conn.commit()

    def replace_all(self, chat_id: int, messages: list[dict]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM conversations WHERE chat_id=?", (chat_id,))
            for seq, message in enumerate(messages):
                tool_calls = message.get("tool_calls")
                self._conn.execute(
                    "INSERT INTO conversations (chat_id, seq, role, content, tool_calls, tool_call_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        chat_id,
                        seq,
                        message["role"],
                        message.get("content"),
                        json.dumps(tool_calls) if tool_calls else None,
                        message.get("tool_call_id"),
                    ),
                )

    def mark_seen(self, email_ids: list[str]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO seen_emails (email_id) VALUES (?)",
                [(eid,) for eid in email_ids],
            )

    def filter_unseen(self, email_ids: list[str]) -> list[str]:
        if not email_ids:
            return []
        placeholders = ",".join("?" * len(email_ids))
        seen = {
            row[0]
            for row in self._conn.execute(
                f"SELECT email_id FROM seen_emails WHERE email_id IN ({placeholders})",
                email_ids,
            )
        }
        return [eid for eid in email_ids if eid not in seen]

    def add_memory(self, chat_id: int, content: str, category: str = "general") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO memories (chat_id, category, content) VALUES (?, ?, ?)",
                (chat_id, category, content),
            )

    def get_profile_memories(self, chat_id: int, limit: int = 30) -> list[str]:
        rows = self._conn.execute(
            "SELECT content FROM memories WHERE chat_id=? AND category='profile' "
            "ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [row["content"] for row in reversed(rows)]

    def search_memories(self, chat_id: int, query: str, limit: int = 5) -> list[str]:
        keywords = query.split()
        if not keywords:
            return []
        conditions = " OR ".join("content LIKE ?" for _ in keywords)
        params = [f"%{kw}%" for kw in keywords]
        rows = self._conn.execute(
            f"SELECT content FROM memories WHERE chat_id=? AND category='general' "
            f"AND ({conditions}) ORDER BY id DESC LIMIT ?",
            (chat_id, *params, limit),
        ).fetchall()
        return [row["content"] for row in rows]

    def _next_seq(self, chat_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq)+1, 0) FROM conversations WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        return row[0]

    def close(self) -> None:
        self._conn.close()
