#!/usr/bin/env python3
"""Add TMDB cast to the catalogue. Uses tmdb_id already stored on each movie."""
import argparse
import datetime
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

TV_TYPES = {"tvSeries", "tvMiniSeries", "tvSpecial"}


def api(path, key, params=None):
    query = dict(params or {})
    query["api_key"] = key
    url = "https://api.themoviedb.org/3/" + path.lstrip("/") + "?" + urllib.parse.urlencode(query)
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "JohnsMovieCatalogue/1.0"})
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError("TMDB credentials rejected") from error
            if error.code == 404:
                return {}
            if error.code not in (429, 500, 502, 503, 504):
                raise
            delay = int(error.headers.get("Retry-After", "0") or 0) or min(30, 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            delay = min(30, 2 ** attempt)
        time.sleep(delay)
    raise RuntimeError("TMDB request failed after retries: " + path)


def ensure_schema(con):
    columns = {row[1] for row in con.execute("pragma table_info(movies)")}
    if "cast_status" not in columns:
        con.execute("ALTER TABLE movies ADD COLUMN cast_status TEXT")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS actors (
          id INTEGER PRIMARY KEY,
          tmdb_person_id INTEGER NOT NULL UNIQUE,
          name TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_actors (
          movie_id INTEGER NOT NULL REFERENCES movies(id),
          actor_id INTEGER NOT NULL REFERENCES actors(id),
          billing_order INTEGER NOT NULL,
          character TEXT,
          PRIMARY KEY (movie_id, actor_id)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_actors_name ON actors(name)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_movie_actors_actor ON movie_actors(actor_id)")
    con.commit()


def actor_id(con, person_id, name):
    row = con.execute("SELECT id FROM actors WHERE tmdb_person_id = ?", (person_id,)).fetchone()
    if row:
        con.execute("UPDATE actors SET name = ? WHERE id = ?", (name, row[0]))
        return row[0]
    cursor = con.execute(
        "INSERT INTO actors (tmdb_person_id, name) VALUES (?, ?)",
        (person_id, name),
    )
    return cursor.lastrowid


def save_cast(con, movie_id, credits):
    con.execute("DELETE FROM movie_actors WHERE movie_id = ?", (movie_id,))
    seen = set()
    for index, person in enumerate((credits.get("cast") or [])[:40]):
        person_id = person.get("id")
        name = (person.get("name") or "").strip()
        if not person_id or not name or person_id in seen:
            continue
        seen.add(person_id)
        order = person.get("order")
        if not isinstance(order, int):
            order = index
        con.execute(
            """
            INSERT INTO movie_actors (movie_id, actor_id, billing_order, character)
            VALUES (?, ?, ?, ?)
            """,
            (movie_id, actor_id(con, person_id, name), order, person.get("character") or None),
        )


def credits_for(tmdb_id, media_type, key):
    kinds = ("tv", "movie") if media_type in TV_TYPES else ("movie", "tv")
    for kind in kinds:
        payload = api(f"{kind}/{tmdb_id}/credits", key)
        if payload:
            return payload
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--key", default=os.getenv("TMDB_API_KEY"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.05)
    args = parser.parse_args()
    if not args.key:
        parser.error("Set TMDB_API_KEY or pass --key")

    con = sqlite3.connect(args.database, timeout=30)
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT id, tmdb_id, media_type
        FROM movies
        WHERE tmdb_id IS NOT NULL
          AND (cast_status IS NULL OR cast_status IN ('error', 'pending'))
        ORDER BY id
        """
    ).fetchall()
    chosen = rows[: args.limit or None]
    ok = missing = errors = 0
    now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    for index, (movie_id, tmdb_id, media_type) in enumerate(chosen, 1):
        try:
            credits = credits_for(tmdb_id, media_type, args.key)
            if credits is None:
                con.execute(
                    "UPDATE movies SET cast_status = 'not_found', tmdb_updated_at = ? WHERE id = ?",
                    (now(), movie_id),
                )
                missing += 1
            else:
                save_cast(con, movie_id, credits)
                con.execute(
                    "UPDATE movies SET cast_status = 'matched', tmdb_updated_at = ? WHERE id = ?",
                    (now(), movie_id),
                )
                ok += 1
        except Exception as error:
            con.execute(
                "UPDATE movies SET cast_status = 'error', tmdb_updated_at = ? WHERE id = ?",
                (now(), movie_id),
            )
            errors += 1
            print(f"ERROR {movie_id}: {error}", flush=True)
        con.commit()
        if index % 100 == 0:
            print(
                f"{index}/{len(chosen)} processed: matched={ok} not_found={missing} errors={errors}",
                flush=True,
            )
        time.sleep(args.delay)
    print(
        f"Finished: matched={ok}, not_found={missing}, errors={errors}. Database: {args.database}",
        flush=True,
    )


if __name__ == "__main__":
    main()
