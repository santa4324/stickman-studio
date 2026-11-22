"""SQLite warehouse: one row per Reel, one row per metric per snapshot."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "reels.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id                 TEXT PRIMARY KEY,
    media_product_type TEXT,
    media_type         TEXT,
    caption            TEXT,
    permalink          TEXT,
    thumbnail_url      TEXT,
    timestamp          TEXT,
    like_count         INTEGER,
    comments_count     INTEGER,
    first_seen         TEXT
);

CREATE TABLE IF NOT EXISTS insights (
    media_id TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    metric   TEXT NOT NULL,
    value    REAL,
    PRIMARY KEY (media_id, taken_at, metric)
);

CREATE TABLE IF NOT EXISTS account_insights (
    taken_at TEXT NOT NULL,
    metric   TEXT NOT NULL,
    value    REAL,
    PRIMARY KEY (taken_at, metric)
);

CREATE TABLE IF NOT EXISTS enriched (
    media_id    TEXT PRIMARY KEY,
    duration_s  REAL,
    transcript  TEXT,
    word_count  INTEGER,
    wpm         REAL,
    hook_text   TEXT,
    cta_text    TEXT,
    frames_dir  TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_insights_lookup ON insights (media_id, taken_at);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def upsert_media(connection, item):
    connection.execute(
        """
        INSERT INTO media (id, media_product_type, media_type, caption, permalink,
                           thumbnail_url, timestamp, like_count, comments_count, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            media_product_type = excluded.media_product_type,
            media_type         = excluded.media_type,
            caption            = excluded.caption,
            permalink          = excluded.permalink,
            thumbnail_url      = excluded.thumbnail_url,
            like_count         = excluded.like_count,
            comments_count     = excluded.comments_count
        """,
        (
            item.get("id"),
            item.get("media_product_type"),
            item.get("media_type"),
            item.get("caption"),
            item.get("permalink"),
            item.get("thumbnail_url"),
            item.get("timestamp"),
            item.get("like_count"),
            item.get("comments_count"),
            now_iso(),
        ),
    )


def save_insights(connection, taken_at, media_id, values):
    rows = [
        (media_id, taken_at, metric, None if value is None else float(value))
        for metric, value in values.items()
        if isinstance(value, (int, float))
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO insights (media_id, taken_at, metric, value) VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def save_account_insights(connection, taken_at, values):
    rows = [
        (taken_at, metric, float(value))
        for metric, value in values.items()
        if isinstance(value, (int, float))
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO account_insights (taken_at, metric, value) VALUES (?, ?, ?)",
        rows,
    )


def latest_insights(connection):
    """{media_id: {metric: value}} using each Reel's most recent snapshot."""
    query = """
        SELECT media_id, metric, value
        FROM insights
        WHERE taken_at = (
            SELECT MAX(taken_at) FROM insights AS newer
            WHERE newer.media_id = insights.media_id
        )
    """
    result = {}
    for row in connection.execute(query):
        result.setdefault(row["media_id"], {})[row["metric"]] = row["value"]
    return result


def all_media(connection):
    return [dict(row) for row in connection.execute("SELECT * FROM media")]


def all_enriched(connection):
    rows = connection.execute("SELECT * FROM enriched")
    return {row["media_id"]: dict(row) for row in rows}


def save_enriched(connection, record):
    connection.execute(
        """
        INSERT INTO enriched (media_id, duration_s, transcript, word_count, wpm,
                              hook_text, cta_text, frames_dir, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(media_id) DO UPDATE SET
            duration_s = excluded.duration_s,
            transcript = excluded.transcript,
            word_count = excluded.word_count,
            wpm        = excluded.wpm,
            hook_text  = excluded.hook_text,
            cta_text   = excluded.cta_text,
            frames_dir = excluded.frames_dir,
            updated_at = excluded.updated_at
        """,
        (
            record["media_id"],
            record.get("duration_s"),
            record.get("transcript"),
            record.get("word_count"),
            record.get("wpm"),
            record.get("hook_text"),
            record.get("cta_text"),
            record.get("frames_dir"),
            now_iso(),
        ),
    )
