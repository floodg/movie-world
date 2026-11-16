"""Bounded, structured operation history. Never store request bodies or credentials."""
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import config

LIMIT = 5000
LEVELS = {'info', 'warning', 'error'}


def clean(value, limit=1000):
    value = str(value or '')
    value = re.sub(r'https?://[^\s<>"\']+', '[address]', value, flags=re.I)
    value = re.sub(r'(?i)\b(api[_-]?key|token|password|authorization|api_hash|phone)\b\s*[=:]\s*[^\s,;]+', r'\1=[redacted]', value)
    value = re.sub(r'\b\+?\d[\d ()-]{8,}\d\b', '[number]', value)
    return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)[:limit]


@contextmanager
def database():
    config.LOCAL.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.LOCAL / 'operations.sqlite', timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
            timestamp INTEGER NOT NULL, received INTEGER NOT NULL, source TEXT NOT NULL,
            level TEXT NOT NULL, operation TEXT NOT NULL, message TEXT NOT NULL, movie_id TEXT NOT NULL)''')
        yield db
        db.commit()
    finally:
        db.close()


def event(operation, message, level='info', movie_id=None):
    """Logging must not make playback or downloads fail."""
    try:
        now = int(time.time() * 1000)
        with database() as db:
            _insert(db, dict(event_id=str(uuid.uuid4()), timestamp=now, source='server',
                             level=level, operation=operation, message=message, movie_id=movie_id))
            _trim(db)
    except (OSError, sqlite3.Error):
        pass


def _insert(db, row):
    db.execute('''INSERT OR IGNORE INTO events
        (event_id,timestamp,received,source,level,operation,message,movie_id) VALUES (?,?,?,?,?,?,?,?)''',
        (row['event_id'], row['timestamp'], int(time.time()*1000), row['source'], row['level'],
         clean(row['operation'], 80), clean(row['message']), clean(row.get('movie_id'), 32)))


def _trim(db):
    db.execute('DELETE FROM events WHERE seq NOT IN (SELECT seq FROM events ORDER BY seq DESC LIMIT ?)', (LIMIT,))


def ingest(rows):
    if not isinstance(rows, list) or len(rows) > 50:
        raise ValueError('Send at most 50 events')
    now = int(time.time()*1000)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('event_id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', row['event_id']):
            raise ValueError('Invalid event ID')
        if row.get('level') not in LEVELS or not isinstance(row.get('timestamp'), int) or isinstance(row['timestamp'], bool) or not 0 < row['timestamp'] < now+86400000:
            raise ValueError('Invalid event metadata')
        if any(not isinstance(row.get(k), str) or len(row[k]) > size for k, size in [('operation',80),('message',1000),('movie_id',32)]):
            raise ValueError('Invalid event text')
    with database() as db:
        for row in rows:
            _insert(db, {**row, 'source': 'android'})
        _trim(db)
    return [r['event_id'] for r in rows]


def history(params):
    def param(name):
        return params.get(name, [''])[0]
    before = param('before')
    clauses, values = [], []
    if before:
        if not before.isascii() or not before.isdigit() or len(before) > 18:
            raise ValueError('Invalid history cursor')
        clauses.append('seq < ?'); values.append(int(before))
    for name, allowed in [('source', {'server','android'}), ('level', LEVELS)]:
        value = param(name)
        if value:
            if value not in allowed:
                raise ValueError('Invalid filter')
            clauses.append(name+' = ?'); values.append(value)
    query = param('q')[:120]
    if query:
        clauses.append("(instr(lower(message),lower(?)) > 0 OR instr(lower(operation),lower(?)) > 0 OR movie_id=?)")
        values.extend([query,query,query])
    with database() as db:
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        rows = [dict(row) for row in db.execute('SELECT * FROM events'+where+' ORDER BY seq DESC LIMIT 201', values)]
    return {'events': rows[:200], 'next': rows[199]['seq'] if len(rows)>200 else None, 'retention': LIMIT}
