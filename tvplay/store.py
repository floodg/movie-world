"""Writable SQLite state for Play on TV: jobs, cache entries, renderers, resume points and paired sessions."""
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  source_size INTEGER NOT NULL,
  play_name TEXT,
  play_size INTEGER,
  mime TEXT,
  duration INTEGER,
  probe TEXT,
  token TEXT NOT NULL UNIQUE,
  ready INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  last_used_at REAL NOT NULL,
  UNIQUE (channel_id, message_id)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  movie_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  renderer_id TEXT NOT NULL,
  renderer_name TEXT,
  idempotency_key TEXT UNIQUE,
  state TEXT NOT NULL,
  detail TEXT,
  error TEXT,
  error_code TEXT,
  retryable INTEGER NOT NULL DEFAULT 0,
  cache_id INTEGER REFERENCES cache_entries(id) ON DELETE SET NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS renderers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  location TEXT NOT NULL,
  info TEXT,
  last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS resume_points (
  movie_id INTEGER PRIMARY KEY,
  position INTEGER NOT NULL,
  duration INTEGER,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  csrf TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_seen REAL NOT NULL
);
"""


def connect():
    config.LOCAL.mkdir(exist_ok=True)
    con = sqlite3.connect(config.JOBS_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init():
    con = connect()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        columns = {row[1] for row in con.execute("PRAGMA table_info(cache_entries)")}
        if "ready" not in columns:
            con.execute("ALTER TABLE cache_entries ADD COLUMN ready INTEGER NOT NULL DEFAULT 1")
        con.commit()
    finally:
        con.close()


def execute(sql, params=()):
    con = connect()
    try:
        cursor = con.execute(sql, params)
        con.commit()
        return cursor.lastrowid
    finally:
        con.close()


def query_all(sql, params=()):
    con = connect()
    try:
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def query_one(sql, params=()):
    con = connect()
    try:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def now():
    return time.time()
