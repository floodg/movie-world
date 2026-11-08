"""Local personal data and Android-compatible Telegram snapshot protocol (stdlib only)."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

MAX_BYTES = 16 * 1024 * 1024
KINDS = ("favourites", "progress")

def number(value):
    return type(value) is int and 0 <= value <= 9223372036854775807

def record(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid personal data record")
    mid = value.get("movieId")
    if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or int(mid) <= 0:
        raise ValueError("Invalid movie ID")
    if not number(value.get("updated")) or not isinstance(value.get("origin"), str) or not value["origin"]:
        raise ValueError("Invalid record version")
    result = dict(movieId=mid, updated=value["updated"], origin=value["origin"], removed=value.get("removed"),
                  position=value.get("position", 0), duration=value.get("duration", 0), finished=value.get("finished", False))
    if not number(result["position"]) or not number(result["duration"]) or type(result["finished"]) is not bool:
        raise ValueError("Invalid watch progress")
    if result["removed"] is not None and not number(result["removed"]):
        raise ValueError("Invalid favourite removal")
    return result

def validate(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("format") != "Movie World Sync" or (type(snapshot.get("version")) is not int or snapshot["version"] != 1):
        raise ValueError("Unsupported Movie World snapshot")
    if not number(snapshot.get("accountId")) or snapshot["accountId"] <= 0 or not isinstance(snapshot.get("deviceId"), str) or not snapshot["deviceId"]:
        raise ValueError("Invalid snapshot identity")
    for kind in KINDS:
        rows = snapshot.get(kind)
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError("Invalid snapshot collection")
        normalized = [record(row) for row in rows]
        if len({r["movieId"] for r in normalized}) != len(rows):
            raise ValueError("Duplicate movie records")
        snapshot[kind] = normalized
    return snapshot

def version(row):
    return row["updated"], row["origin"]

def covers(retained, older):
    for kind in KINDS:
        newest = {}
        for snapshot in retained:
            for row in snapshot[kind]:
                if row["movieId"] not in newest or version(row) > version(newest[row["movieId"]]):
                    newest[row["movieId"]] = row
        for row in older[kind]:
            current = newest.get(row["movieId"])
            if current is None or (current != row and version(current) <= version(row)):
                return False
    return True

def attachment(filename):
    return isinstance(filename, str) and filename.replace("_", "-").startswith("movie-world-sync-v1-") and filename.endswith(".json")

def encode(snapshot):
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))

class PersonalStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS personal_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS personal_records (owner TEXT NOT NULL, kind TEXT NOT NULL,
                    movie_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,kind,movie_id));
                CREATE TABLE IF NOT EXISTS resume_points (movie_id INTEGER PRIMARY KEY, position INTEGER NOT NULL,
                    duration INTEGER, updated_at REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO personal_meta VALUES ('owner','local')")
            db.execute("INSERT OR IGNORE INTO personal_meta VALUES ('device',?)", (str(uuid.uuid4()),))
        self.device = self.meta("device")
        # Adopt existing LAN resume records once, before the first Telegram reconciliation.
        with self.connect() as db:
            if not self._meta(db, "resume-imported"):
                for row in db.execute("SELECT * FROM resume_points").fetchall():
                    item = record(dict(movieId=str(row["movie_id"]), updated=int(row["updated_at"]*1000), origin=self.device,
                                       position=row["position"], duration=row["duration"] or 0))
                    self._merge(db, self._meta(db,"owner"), "progress", item)
                self._set(db,"resume-imported","yes")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            with db: yield db
        finally: db.close()

    def _meta(self, db, key):
        row = db.execute("SELECT value FROM personal_meta WHERE key=?",(key,)).fetchone()
        return row[0] if row else None

    def _set(self, db, key, value):
        db.execute("INSERT OR REPLACE INTO personal_meta VALUES (?,?)",(key,str(value)))

    def meta(self, key):
        with self.connect() as db: return self._meta(db,key)

    def set_meta(self,key,value):
        with self.connect() as db: self._set(db,key,value)

    def bind(self, account):
        if not number(account) or account <= 0: raise ValueError("Invalid Telegram account")
        owner=str(account)
        with self.connect() as db:
            old=self._meta(db,"owner")
            if old==owner:return
            if old=="local": db.execute("UPDATE personal_records SET owner=? WHERE owner='local'",(owner,))
            self._set(db,"owner",owner)
            db.execute("DELETE FROM resume_points")
            for row in db.execute("SELECT payload FROM personal_records WHERE owner=? AND kind='progress'",(owner,)).fetchall():
                self._mirror(db,json.loads(row[0]))

    def _mirror(self,db,row):
        if row["finished"] or row["position"] < 60:
            db.execute("DELETE FROM resume_points WHERE movie_id=?",(int(row["movieId"]),))
        else:
            db.execute("INSERT OR REPLACE INTO resume_points VALUES (?,?,?,?)",
                (int(row["movieId"]),row["position"],row["duration"],row["updated"]/1000))

    def _merge(self,db,owner,kind,row):
        old=db.execute("SELECT payload FROM personal_records WHERE owner=? AND kind=? AND movie_id=?",(owner,kind,row["movieId"])).fetchone()
        if old and version(json.loads(old[0])) >= version(row): return False
        db.execute("INSERT OR REPLACE INTO personal_records VALUES (?,?,?,?)",(owner,kind,row["movieId"],encode(row)))
        if kind=="progress": self._mirror(db,row)
        return True

    def snapshot(self):
        with self.connect() as db:
            owner=self._meta(db,"owner")
            result=dict(format="Movie World Sync",version=1,accountId=int(owner) if owner!="local" else 0,deviceId=self.device)
            for kind in KINDS:
                result[kind]=[json.loads(r[0]) for r in db.execute("SELECT payload FROM personal_records WHERE owner=? AND kind=? ORDER BY movie_id",(owner,kind))]
            return result

    def merge(self,snapshots):
        snapshots=[validate(s) for s in snapshots]
        with self.connect() as db:
            owner=self._meta(db,"owner")
            if any(str(s["accountId"])!=owner for s in snapshots): raise ValueError("Snapshot belongs to another account")
            for snapshot in snapshots:
                for kind in KINDS:
                    for row in snapshot[kind]: self._merge(db,owner,kind,row)

    def browser_merge(self,body):
        if not isinstance(body,dict) or not isinstance(body.get("scope"),str): raise ValueError("Invalid personal data request")
        normalized={kind:[record({**r,"origin":self.device}) for r in body.get(kind,[])] for kind in KINDS}
        with self.connect() as db:
            owner=self._meta(db,"owner")
            if owner!=body["scope"]: raise ValueError("Account changed; reload this page")
            for kind in KINDS:
                for row in normalized[kind]: self._merge(db,owner,kind,row)

    def progress(self,movie_id,position,duration,finished=False):
        """Record actual local playback; timestamps advance even after a remote device's clock skew."""
        with self.connect() as db:
            owner=self._meta(db,"owner")
            old=db.execute("SELECT payload FROM personal_records WHERE owner=? AND kind='progress' AND movie_id=?",(owner,str(movie_id))).fetchone()
            previous=json.loads(old[0]) if old else None
            row=record(dict(movieId=str(movie_id),updated=max(int(time.time()*1000),(previous["updated"]+1) if previous else 0),origin=self.device,
                            position=int(position),duration=int(duration or 0),finished=bool(finished)))
            if previous and all(previous[k]==row[k] for k in ("position","duration","finished")):return
            self._merge(db,owner,"progress",row)
