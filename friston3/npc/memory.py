"""Explicit local memories with provenance; no LLM-based fact extraction.

Persisted dialogue is an audit log only. Assistant messages never enter recall
evidence. NpcTalkSkill separately carries bounded live-session dialogue for
discourse continuity; persisted model replies are not reloaded as facts.
User reports and actual skill results live in separate tables and remain labelled.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from friston3.config import PROJECT_ROOT


class MemoryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else PROJECT_ROOT / "data" / "runtime" / "npc_memory.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid4().hex
        with closing(sqlite3.connect(str(self.path))) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY, statement TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL DEFAULT 'user_report', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY, skill TEXT NOT NULL, success INTEGER NOT NULL,
                    message TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'skill_result',
                    created_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def log_turn(self, user_text, reply):
        # An audit write must not hold a visitor's reply behind another writer.
        with closing(sqlite3.connect(str(self.path), timeout=0.1)) as db, db:
            db.executemany(
                "INSERT INTO conversations(session_id,role,content,created_at) VALUES(?,?,?,?)",
                [(self.session_id, role, content, self._now())
                 for role, content in (("user", user_text), ("assistant", reply))],
            )
            # Bounded audit history. Explicit memories are never silently evicted.
            db.execute("DELETE FROM conversations WHERE id NOT IN "
                       "(SELECT id FROM conversations ORDER BY id DESC LIMIT 2000)")

    def remember(self, statement):
        with closing(sqlite3.connect(str(self.path))) as db, db:
            db.execute("INSERT INTO user_memories(statement,source,created_at) VALUES(?,?,?) "
                       "ON CONFLICT(statement) DO UPDATE SET created_at=excluded.created_at",
                       (statement, "user_report", self._now()))

    def forget(self, query):
        """Delete literal substring matches, including their audit-log copies."""
        with closing(sqlite3.connect(str(self.path))) as db, db:
            count = db.execute("DELETE FROM user_memories WHERE instr(statement,?)>0", (query,)).rowcount
            db.execute("DELETE FROM conversations WHERE instr(content,?)>0", (query,))
            return count

    def record_execution(self, skill_name, result):
        if skill_name not in ("take_photo", "auto_photo"):
            return
        if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
            return
        message = str(result.get("message", ""))[:500]
        with closing(sqlite3.connect(str(self.path))) as db, db:
            db.execute("INSERT INTO execution_events(skill,success,message,source,created_at) "
                       "VALUES(?,?,?,?,?)", (skill_name, int(result["success"]), message,
                                            "skill_result", self._now()))

    def recent_execution_events(self, limit=2):
        """Camera outcomes prove neither encounters nor travel."""
        with closing(sqlite3.connect(str(self.path))) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT id,skill,success,message,source,created_at FROM execution_events "
                "WHERE source='skill_result' AND skill IN ('take_photo','auto_photo') "
                "ORDER BY id DESC LIMIT ?", (max(1, min(10, int(limit))),))]

    @staticmethod
    def _terms(text):
        terms = set(re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()))
        for part in re.findall(r"[\u3400-\u9fff]+", text):
            terms.update(part[i:i + 2] for i in range(len(part) - 1))
        return terms - {"记得", "记住", "什么", "一下", "之前", "告诉", "我的", "我是", "你还"}

    def recall(self, user_text, limit=3, *, include_execution=True):
        terms = self._terms(user_text)
        general = any(word in user_text for word in ("记得我", "关于我的", "记住了什么", "我说过什么"))
        with closing(sqlite3.connect(str(self.path), timeout=0.1)) as db:
            rows = db.execute("SELECT statement,created_at FROM user_memories ORDER BY id DESC LIMIT 500").fetchall()
            scored = [(len(terms & self._terms(statement)), index, statement, timestamp)
                      for index, (statement, timestamp) in enumerate(rows)]
            selected = sorted((item for item in scored if item[0] or general),
                              key=lambda item: (-item[0], item[1]))[:limit]
            lines = [f"[user_report，用户自述，{stamp[:10]}] {statement[:180]}"
                     for _, _, statement, stamp in selected]
            if include_execution and any(word in user_text for word in ("上次", "刚才", "拍过", "拍了", "做过", "经历", "回忆")):
                events = db.execute("SELECT skill,success,message,created_at FROM execution_events "
                                    "ORDER BY id DESC LIMIT 2").fetchall()
                event_lines = [f"[skill_result，真实执行，{stamp}] {skill}："
                               f"{'成功' if success else '失败'}，{message[:140]}"
                               for skill, success, message, stamp in events]
                # Actual outcomes must survive the small prompt budget first.
                lines = event_lines + lines
        return "\n".join(lines)

    def clear_all(self):
        counts = {}
        with closing(sqlite3.connect(str(self.path))) as db, db:
            for table in ("conversations", "user_memories", "execution_events"):
                counts[table] = db.execute(f"DELETE FROM {table}").rowcount
        return counts
