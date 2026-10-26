"""QQ 机器人的记忆库：SQLite，零第三方依赖。

三层记忆，对应三种"想起来"的需求：

  episodes  情景记忆 —— 谁在什么时候说了什么，带时间戳，可按关键词检索
  facts     语义记忆 —— 稳定的事实（"她叫小周""在找工作"），键值对
  summary   滚动摘要 —— 每个会话的近期概括，控制上下文长度（预留）

检索用 2-gram 切片 + LIKE，不依赖分词库，中文也能用。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session    TEXT NOT NULL,
    msg_id     TEXT,
    speaker_id TEXT,
    speaker    TEXT,
    role       TEXT NOT NULL,        -- user / bot / system
    text       TEXT NOT NULL,
    kind       TEXT DEFAULT 'chat',  -- chat / event / decision
    ts         INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ep_uniq ON episodes(session, msg_id) WHERE msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ep_session_ts ON episodes(session, ts DESC);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,        -- global / session:group:123
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    updated_at INTEGER NOT NULL,
    UNIQUE(scope, key)
);

-- 事件级去重：同一条消息只处理一次。QQ 会重推事件，没有这张表会重复回复。
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    ts  INTEGER NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _terms(query: str) -> list[str]:
    """把查询切成 2-gram，中文无空格也能匹配。"""
    q = "".join(ch for ch in query if ch.strip())
    if len(q) <= 2:
        return [q] if q else []
    return list({q[i:i + 2] for i in range(len(q) - 1)})


def log_message(session: str, *, msg_id: str | None, speaker_id: str | None,
                speaker: str | None, role: str, text: str,
                kind: str = "chat", ts: int | None = None) -> bool:
    """写入一条消息。msg_id 重复则跳过（幂等）。返回是否真的写入。"""
    if not text or not text.strip():
        return False
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO episodes(session,msg_id,speaker_id,speaker,role,text,kind,ts) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (session, msg_id, speaker_id, speaker, role, text.strip(), kind, ts or int(time.time())),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def recent(session: str, limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT speaker, role, text, kind, ts FROM episodes "
            "WHERE session=? ORDER BY ts DESC, id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def search(query: str, session: str | None = None, limit: int = 8) -> list[dict]:
    """按关键词找历史片段。命中的 2-gram 越多排得越前。

    用 OR 而不是 AND：中文 2-gram 会切出「水公」这种不存在的组合，
    要求全部命中等于永远搜不到。宁可多召回再按命中数排序。
    """
    terms = _terms(query)
    if not terms:
        return []
    where = " OR ".join(["text LIKE ?"] * len(terms))
    args = [f"%{t}%" for t in terms]
    sql = f"SELECT session, speaker, role, text, ts FROM episodes WHERE ({where})"
    if session:
        sql += " AND session=?"
        args.append(session)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit * 6)

    with _conn() as conn:
        rows = conn.execute(sql, args).fetchall()

    def score(text: str) -> int:
        return sum(1 for t in terms if t in text)

    items = [dict(r) for r in rows]
    items.sort(key=lambda r: (score(r["text"]), r["ts"]), reverse=True)
    return [r for r in items if score(r["text"]) > 0][:limit]


def set_fact(key: str, value: str, scope: str = "global", confidence: float = 1.0) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO facts(scope,key,value,confidence,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, "
            "confidence=excluded.confidence, updated_at=excluded.updated_at",
            (scope, key, value, confidence, int(time.time())),
        )


def get_facts(scope: str = "global", limit: int = 30) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT key, value, confidence, updated_at FROM facts "
            "WHERE scope=? ORDER BY updated_at DESC LIMIT ?",
            (scope, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with _conn() as conn:
        ep = conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
        fa = conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        se = conn.execute("SELECT COUNT(DISTINCT session) c FROM episodes").fetchone()["c"]
    return {"episodes": ep, "facts": fa, "sessions": se, "db": str(DB_PATH)}


# ---------------------------------------------------------------- 事件去重
def mark_seen(key: str) -> bool:
    """标记事件已处理。返回 True 表示是新的（首次见到），False 表示重复。

    QQ 会重推事件，不挡就会重复回复。用 INSERT OR IGNORE 的 rowcount 判断，
    一条 SQL 完成「查+写」，没有竞态窗口。
    """
    if not key:
        return True
    with _conn() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO seen(key, ts) VALUES(?, ?)",
                           (str(key), int(time.time())))
        return cur.rowcount == 1


def prune_seen(days: int = 7) -> int:
    """清理过期去重记录，避免无限增长。"""
    cutoff = int(time.time()) - days * 86400
    with _conn() as conn:
        cur = conn.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
        return cur.rowcount


def seen_stats() -> dict:
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM seen").fetchone()["c"]
    return {"seen": total}


if __name__ == "__main__":
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
