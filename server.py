#!/usr/bin/env python3
"""Local genre browser for the Movie World SQLite catalogue. Stdlib only."""
import json
import argparse
import re
import ssl
import sqlite3
import secrets
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DB = ROOT / "sql-lite" / "movie-world.sqlite"
HOST = "127.0.0.1"
PORT = 8765
SEARCH_LIMIT = 48
EXCLUDED_GENRE = "Adult"
MAX_BODY = 8192
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
TV_ROUTES = (("api", "tv"), ("api", "tvs"), ("api", "playback"), ("api", "resume"))


def not_excluded(movie_id="m.id"):
    return f"""
        NOT EXISTS (
          SELECT 1
          FROM movie_genres excluded_mg
          JOIN genres excluded_g ON excluded_g.id = excluded_mg.genre_id
          WHERE excluded_mg.movie_id = {movie_id}
            AND excluded_g.name = '{EXCLUDED_GENRE}'
        )
    """

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
}


def connect():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def query_all(sql, params=()):
    con = connect()
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def query_one(sql, params=()):
    con = connect()
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


def genres():
    rows = query_all(
        f"""
        SELECT
          g.id,
          g.name,
          COUNT(mg.movie_id) AS movie_count,
          COALESCE(
            (
              SELECT COALESCE(m.backdrop_url, m.poster_url)
              FROM movies m
              JOIN movie_genres mg2 ON mg2.movie_id = m.id
            WHERE mg2.genre_id = g.id
              AND {not_excluded()}
            ORDER BY m.imdb_rating IS NULL, m.imdb_rating DESC, m.imdb_votes DESC
            LIMIT 1
            ),
            (
              SELECT COALESCE(m.backdrop_url, m.poster_url)
              FROM movies m
              JOIN movie_genres mg2 ON mg2.movie_id = m.id
              WHERE mg2.genre_id = g.id
                AND {not_excluded()}
              ORDER BY m.imdb_rating IS NULL, m.imdb_rating DESC, m.imdb_votes DESC
              LIMIT 1 OFFSET 1
            )
          ) AS backdrop_url
        FROM genres g
        JOIN movie_genres mg ON mg.genre_id = g.id
        WHERE g.name != '{EXCLUDED_GENRE}'
          AND {not_excluded("mg.movie_id")}
        GROUP BY g.id
        ORDER BY movie_count DESC, g.name COLLATE NOCASE
        """
    )
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "movie_count": row["movie_count"],
            "backdrop_url": row["backdrop_url"],
        }
        for row in rows
    ]


def genre_movies(genre_id):
    genre = query_one(
        "SELECT id, name FROM genres WHERE id = ? AND name != ?",
        (genre_id, EXCLUDED_GENRE),
    )
    if genre is None:
        return None
    rows = query_all(
        f"""
        SELECT
          m.id,
          COALESCE(m.title, m.source_title) AS title,
          m.release_year,
          m.imdb_rating,
          m.imdb_votes,
          m.poster_url,
          m.media_type
        FROM movies m
        JOIN movie_genres mg ON mg.movie_id = m.id
        WHERE mg.genre_id = ?
          AND {not_excluded()}
        ORDER BY m.imdb_rating IS NULL, m.imdb_rating DESC, m.imdb_votes DESC,
                 title COLLATE NOCASE
        """,
        (genre_id,),
    )
    return [movie_card(row) for row in rows]


def movie_card(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "year": row["release_year"],
        "rating": row["imdb_rating"],
        "votes": row["imdb_votes"],
        "poster_url": row["poster_url"],
        "media_type": row["media_type"],
    }


def continue_watching_movies(points):
    ids = [point["movie_id"] for point in points if 0 < point["position"] < point["duration"]]
    cards = {}
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        placeholders = ", ".join("?" for _ in batch)
        rows = query_all(
            f"""
            SELECT m.id, COALESCE(m.title, m.source_title) AS title, m.release_year,
                   m.imdb_rating, m.imdb_votes, m.poster_url, m.media_type
            FROM movies m
            WHERE m.id IN ({placeholders}) AND {not_excluded()}
            """,
            batch,
        )
        cards.update((row["id"], movie_card(row)) for row in rows)
    return [cards[movie_id] for movie_id in ids if movie_id in cards]


def table_exists(name):
    row = query_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    return row is not None


def search_actors(raw_query):
    term = " ".join(raw_query.split())[:80]
    if len(term) < 2 or not table_exists("actors"):
        return {"query": term, "actors": []}
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"
    rows = query_all(
        f"""
        SELECT a.id, a.name, COUNT(ma.movie_id) AS movie_count
        FROM actors a
        JOIN movie_actors ma ON ma.actor_id = a.id
        JOIN movies m ON m.id = ma.movie_id
        WHERE a.name LIKE ? ESCAPE '\\'
          AND {not_excluded()}
        GROUP BY a.id
        ORDER BY
          CASE WHEN a.name LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END,
          movie_count DESC,
          a.name COLLATE NOCASE
        LIMIT 12
        """,
        (contains, prefix),
    )
    return {
        "query": term,
        "actors": [
            {"id": row["id"], "name": row["name"], "movie_count": row["movie_count"]}
            for row in rows
        ],
    }


def actor_movies(actor_id):
    if not table_exists("actors"):
        return None
    actor = query_one("SELECT id, name FROM actors WHERE id = ?", (actor_id,))
    if actor is None:
        return None
    rows = query_all(
        f"""
        SELECT
          m.id,
          COALESCE(m.title, m.source_title) AS title,
          m.release_year,
          m.imdb_rating,
          m.imdb_votes,
          m.poster_url,
          m.media_type
        FROM movies m
        JOIN movie_actors ma ON ma.movie_id = m.id
        WHERE ma.actor_id = ?
          AND {not_excluded()}
        ORDER BY m.imdb_rating IS NULL, m.imdb_rating DESC, m.imdb_votes DESC,
                 title COLLATE NOCASE
        """,
        (actor_id,),
    )
    return {
        "id": actor["id"],
        "name": actor["name"],
        "total": len(rows),
        "movies": [movie_card(row) for row in rows],
    }


def movie_cast(movie_id):
    if not table_exists("movie_actors"):
        return []
    rows = query_all(
        """
        SELECT a.id, a.name
        FROM movie_actors ma
        JOIN actors a ON a.id = ma.actor_id
        WHERE ma.movie_id = ?
        ORDER BY ma.billing_order, a.name COLLATE NOCASE
        LIMIT 12
        """,
        (movie_id,),
    )
    return [{"id": row["id"], "name": row["name"]} for row in rows]


YEAR_MIN = 1880
YEAR_MAX = 2100
MAX_YEAR_RANGES = 24


def parse_year_ranges(raw):
    if not isinstance(raw, str) or not raw.strip():
        return []
    ranges = []
    seen = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if len(ranges) >= MAX_YEAR_RANGES:
            break
        if "-" in token:
            left, right = token.split("-", 1)
        else:
            left = right = token
        if not left.isdigit() or not right.isdigit():
            continue
        start = int(left)
        end = int(right)
        if start > end:
            start, end = end, start
        if start < YEAR_MIN or end > YEAR_MAX:
            continue
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        ranges.append(key)
    return ranges


def year_sql(ranges):
    if not ranges:
        return "", []
    clause = " AND (" + " OR ".join("m.release_year BETWEEN ? AND ?" for _ in ranges) + ")"
    params = [year for pair in ranges for year in pair]
    return clause, params


def search_movies(raw_query, raw_years=""):
    term = " ".join(raw_query.split())[:80]
    if not term:
        return {"query": "", "total": 0, "movies": []}
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"
    year_clause, year_params = year_sql(parse_year_ranges(raw_years))
    rows = query_all(
        f"""
        SELECT
          m.id,
          COALESCE(m.title, m.source_title) AS title,
          m.release_year,
          m.imdb_rating,
          m.imdb_votes,
          m.poster_url,
          m.media_type,
          COUNT(*) OVER() AS match_count
        FROM movies m
        WHERE COALESCE(m.title, m.source_title) LIKE ? ESCAPE '\\'
          AND {not_excluded()}
          {year_clause}
        ORDER BY
          CASE
            WHEN COALESCE(m.title, m.source_title) LIKE ? ESCAPE '\\' THEN 0
            ELSE 1
          END,
          m.imdb_rating IS NULL, m.imdb_rating DESC, m.imdb_votes DESC,
          title COLLATE NOCASE
        LIMIT ?
        """,
        (contains, *year_params, prefix, SEARCH_LIMIT),
    )
    if not rows:
        return {"query": term, "total": 0, "movies": []}
    return {
        "query": term,
        "total": rows[0]["match_count"],
        "movies": [movie_card(row) for row in rows],
    }


def movie_detail(movie_id):
    row = query_one(
        f"""
        SELECT
          m.id,
          COALESCE(m.title, m.source_title) AS title,
          m.release_year,
          m.imdb_rating,
          m.imdb_votes,
          m.poster_url,
          m.backdrop_url,
          m.overview,
          m.runtime_minutes,
          m.telegram_url,
          m.media_type,
          (
            SELECT group_concat(g.name, ', ')
            FROM movie_genres mg
            JOIN genres g ON g.id = mg.genre_id
            WHERE mg.movie_id = m.id
              AND g.name != '{EXCLUDED_GENRE}'
          ) AS genres
        FROM movies m
        WHERE m.id = ?
          AND {not_excluded()}
        """,
        (movie_id,),
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "year": row["release_year"],
        "rating": row["imdb_rating"],
        "votes": row["imdb_votes"],
        "poster_url": row["poster_url"],
        "backdrop_url": row["backdrop_url"],
        "overview": row["overview"],
        "runtime_minutes": row["runtime_minutes"],
        "telegram_url": row["telegram_url"],
        "media_type": row["media_type"],
        "genres": row["genres"],
        "cast": movie_cast(movie_id),
    }


def tizen_home(day):
    """Match Android Catalogue.featured/topRated and its six popular genre rows."""
    rank = "m.imdb_rating IS NULL, m.imdb_rating DESC, m.imdb_votes DESC, m.title COLLATE NOCASE"
    columns = "m.id, COALESCE(m.title,m.source_title) AS title,m.release_year,m.imdb_rating,m.imdb_votes,m.poster_url,m.media_type"
    top = query_all(f"SELECT {columns} FROM movies m WHERE {not_excluded()} AND m.imdb_rating IS NOT NULL ORDER BY {rank} LIMIT 24")
    featured = query_one(f"SELECT m.id FROM movies m WHERE {not_excluded()} AND m.backdrop_url IS NOT NULL AND m.imdb_rating IS NOT NULL ORDER BY {rank} LIMIT 1 OFFSET ?", (day % 40,))
    if featured is None:
        featured = query_one(f"SELECT m.id FROM movies m WHERE {not_excluded()} AND m.backdrop_url IS NOT NULL ORDER BY {rank} LIMIT 1")
    available = genres()
    rows = []
    for genre in available[:6]:
        movies = query_all(f"SELECT {columns} FROM movies m JOIN movie_genres mg ON mg.movie_id=m.id WHERE mg.genre_id=? AND {not_excluded()} ORDER BY {rank} LIMIT 24", (genre['id'],))
        rows.append({"genre": genre, "movies": [movie_card(m) for m in movies]})
    return {"featured": movie_detail(featured['id']) if featured else None, "top_rated": [movie_card(m) for m in top], "genres": available, "rows": rows}


def tizen_search(params):
    term = ' '.join(params.get('q', [''])[0].split())[:80]
    clause, years = year_sql(parse_year_ranges(params.get('years', [''])[0]))
    conditions = [not_excluded()]
    values = []
    genre = params.get('genre', [''])[0]
    if genre:
        conditions.append('EXISTS (SELECT 1 FROM movie_genres mg WHERE mg.movie_id=m.id AND mg.genre_id=?)')
        values.append(genre)
    actor_id = params.get('actor_id', [''])[0] if params.get('mode', ['title'])[0] == 'actor' else ''
    if actor_id:
        conditions.append('EXISTS (SELECT 1 FROM movie_actors ma WHERE ma.movie_id=m.id AND ma.actor_id=?)')
        values.append(actor_id)
    elif term:
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        if params.get('mode', ['title'])[0] == 'actor':
            if not table_exists('actors'):
                return {"movies": []}
            conditions.append("EXISTS (SELECT 1 FROM movie_actors ma JOIN actors a ON a.id=ma.actor_id WHERE ma.movie_id=m.id AND a.name LIKE ? ESCAPE '\\')")
        else:
            conditions.append("COALESCE(m.title,m.source_title) LIKE ? ESCAPE '\\'")
        values.append('%' + escaped + '%')
    limit = '' if actor_id else 'LIMIT 120'
    rows = query_all(f"""SELECT m.id,COALESCE(m.title,m.source_title) AS title,m.release_year,m.imdb_rating,m.imdb_votes,m.poster_url,m.media_type
        FROM movies m WHERE {' AND '.join(conditions)} {clause}
        ORDER BY m.imdb_rating IS NULL,m.imdb_rating DESC,m.imdb_votes DESC,title COLLATE NOCASE {limit}""", (*values, *years))
    return {"movies": [movie_card(m) for m in rows]}


def tizen_similar(movie_id):
    rows = query_all(f"""SELECT m.id,COALESCE(m.title,m.source_title) AS title,m.release_year,m.imdb_rating,m.imdb_votes,m.poster_url,m.media_type
        FROM movies m JOIN movie_genres mg ON mg.movie_id=m.id WHERE m.id<>? AND {not_excluded()}
        AND mg.genre_id IN (SELECT genre_id FROM movie_genres WHERE movie_id=?) GROUP BY m.id
        ORDER BY COUNT(*) DESC,m.imdb_rating IS NULL,m.imdb_rating DESC,m.imdb_votes DESC LIMIT 24""", (movie_id,movie_id))
    return [movie_card(m) for m in rows]


def static_file(url_path):
    if url_path == "/":
        url_path = "/index.html"
    elif url_path in ("/tv", "/tv/"):
        url_path = "/tv/index.html"
    rel = url_path.lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    candidate = (WEB / rel).resolve()
    web_root = WEB.resolve()
    if candidate != web_root and web_root not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


def is_tv_route(path):
    parts = tuple(part for part in path.split("/") if part)
    return parts[:2] in TV_ROUTES


class Handler(BaseHTTPRequestHandler):
    tv = None
    personal = None
    personal_sync = None
    secure = False

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/api/personal", "/api/personal/merge", "/api/personal/sync"):
            self.handle_personal("GET", path)
            return
        if path.startswith("/api/"):
            if is_tv_route(path):
                self.handle_tv("GET", path, parsed.query)
                return
            self.handle_api(path, parsed.query)
            return
        self.serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/api/personal", "/api/personal/merge", "/api/personal/sync"):
            self.handle_personal("POST", path)
            return
        if path.startswith("/api/tizen/"):
            self.handle_tizen(path)
            return
        if re.fullmatch(r"/api/movies/[0-9]+/upload", path):
            self.handle_upload(path)
            return
        if not is_tv_route(path):
            self.send_json({"error": "Not found"}, 404)
            return
        self.handle_tv("POST", path, parsed.query)

    def handle_personal(self, method, path):
        if self.personal is None:
            self.send_json({"error": "Personal storage is unavailable"}, 503)
            return
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            self.send_json({"error": "Cross-site requests are not allowed"}, 403)
            return
        origin = self.headers.get("Origin")
        expected = ("https" if self.secure else "http") + "://" + self.headers.get("Host", "")
        if origin and origin != expected:
            self.send_json({"error": "Cross-origin requests are not allowed"}, 403)
            return
        if method == "GET" and path != "/api/personal":
            self.send_json({"error": "Method not allowed"}, 405)
            return
        if method == "POST":
            problem = self.cross_site_problem()
            if problem:
                self.send_json({"error": problem}, 403)
                return
            body = self.read_json()
            if body is None: return
            try:
                if path == "/api/personal/merge": self.personal.browser_merge(body)
                elif path != "/api/personal/sync":
                    self.send_json({"error": "Method not allowed"}, 405)
                    return
            except (ValueError, TypeError, KeyError):
                self.send_json({"error": "Invalid personal data or account changed; reload this page"}, 409)
                return
            if self.personal_sync: self.personal_sync.request(force=path == "/api/personal/sync")
        snapshot = self.personal.snapshot()
        status = self.personal_sync.status if self.personal_sync else {
            "message": "Saved on this PC; install playback requirements and configure PC Telegram for sync",
            "lastSync": 0, "pending": True,
        }
        self.send_json({"scope": str(snapshot["accountId"]) if snapshot["accountId"] else "local", "snapshot": snapshot, "status": status})

    def handle_upload(self, path):
        from tvplay.errors import PlaybackError
        from tvplay.cache import disk_full
        self.close_connection = True
        if self.tv is None:
            self.send_json({"error": "Play on TV is not enabled on this server."}, 404)
            return
        problem = self.cross_site_problem("application/octet-stream")
        if problem:
            self.send_json({"error": problem}, 403)
            return
        entry = None
        complete = False
        manager = self.tv.manager
        try:
            if self.headers.get("Transfer-Encoding"):
                raise PlaybackError("Upload requires a file size.", "bad_request")
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            movie = movie_detail(int(path.split("/")[3]))
            if movie is None:
                self.send_json({"error": "Movie not found"}, 404)
                return
            ext = Path(unquote(self.headers.get("X-File-Name", ""))).suffix.lower()
            entry = self.tv.call(manager.begin_upload(movie, size, ext))
            self.connection.settimeout(120)
            with manager.cache.path(entry["source_name"]).open("xb") as target:
                remaining = size
                while remaining:
                    block = self.rfile.read(min(1024 * 1024, remaining))
                    if not block:
                        raise PlaybackError("Upload interrupted. Select the file to try again.", "incomplete")
                    target.write(block)
                    remaining -= len(block)
            self.tv.call(manager.finish_upload(entry["id"], True), timeout=180)
            complete = True
            self.send_json({"uploaded": True, "movie_id": movie["id"]})
        except PlaybackError as exc:
            self.send_json({"error": exc.message, "code": exc.code}, 400)
        except (OSError, TimeoutError) as exc:
            self.send_json({"error": "The server disk is full." if disk_full(exc) else "Upload interrupted. Please try again."}, 400)
        finally:
            if entry is not None and not complete:
                self.tv.call(manager.finish_upload(entry["id"], False))

    def handle_tv(self, method, path, query):
        parts = [part for part in path.split("/") if part]
        if self.tv is None:
            if parts == ["api", "tv", "session"]:
                self.send_json({"enabled": False})
            else:
                self.send_json({"error": "Play on TV is not enabled on this server."}, 404)
            return
        from tvplay.errors import PlaybackError
        from tvplay.jobs import Busy, NotFound

        body = {}
        if method == "POST":
            problem = self.cross_site_problem()
            if problem:
                self.send_json({"error": problem, "code": "forbidden"}, 403)
                return
            body = self.read_json()
            if body is None:
                return
        if parts == ["api", "tv", "session"] and method == "GET":
            self.send_json({"enabled": True})
            return
        manager = self.tv.manager
        try:
            if parts == ["api", "tvs"] and method == "GET":
                refresh = parse_qs(query).get("refresh", [""])[0] == "1"
                self.send_json(self.tv.call(manager.list_renderers(refresh)))
            elif parts == ["api", "playback"] and method == "POST":
                movie_id = body.get("movie_id")
                renderer_id = body.get("renderer_id")
                valid_id = isinstance(movie_id, int) and not isinstance(movie_id, bool)
                if not valid_id or not isinstance(renderer_id, str) or len(renderer_id) > 200 or renderer_id.startswith("tizen:"):
                    self.send_json({"error": "Choose a title and a TV.", "code": "bad_request"}, 400)
                    return
                movie = movie_detail(movie_id)
                if movie is None:
                    self.send_json({"error": "Movie not found", "code": "not_found"}, 404)
                    return
                job, created = self.tv.call(
                    manager.create(
                        movie, renderer_id, body.get("idempotency_key"), body.get("replace") is True, body.get("start_at")
                    )
                )
                self.send_json({"job": job}, 202 if created else 200)
            elif parts == ["api", "resume"] and method == "GET":
                points = self.tv.call(manager.resume_points())
                self.send_json({"points": points, "movies": continue_watching_movies(points)})
            elif parts == ["api", "resume"] and method == "POST":
                mirrored = self.tv.call(manager.mirror_resume_points(body.get("points") or []))
                self.send_json({"points": mirrored})
            elif len(parts) == 3 and parts[:2] == ["api", "resume"] and method == "GET" and parts[2].isascii() and parts[2].isdigit():
                self.send_json({"resume": self.tv.call(manager.resume_point(int(parts[2])))})
            elif parts == ["api", "playback", "active"] and method == "GET":
                self.send_json({"job": self.tv.call(manager.active())})
            elif len(parts) == 3 and parts[1] == "playback" and method == "GET" and JOB_ID.match(parts[2]):
                self.send_json({"job": self.tv.call(manager.get(parts[2]))})
            elif len(parts) == 4 and parts[1] == "playback" and method == "POST" and JOB_ID.match(parts[2]):
                if parts[3] == "cancel":
                    self.send_json({"job": self.tv.call(manager.cancel(parts[2]))})
                elif parts[3] == "control":
                    job = self.tv.call(manager.control(parts[2], body.get("action"), body.get("position")))
                    self.send_json({"job": job})
                else:
                    self.send_json({"error": "Not found"}, 404)
            else:
                self.send_json({"error": "Not found"}, 404)
        except PlaybackError as exc:
            self.send_json({"error": exc.message, "code": exc.code, "retryable": exc.retryable}, 400)
        except Busy as exc:
            self.send_json(
                {"error": f"The TV is busy with {exc.job['title']}.", "code": "busy", "job": exc.job}, 409
            )
        except NotFound:
            self.send_json({"error": "Playback not found", "code": "not_found"}, 404)
        except TimeoutError as exc:
            self.send_json({"error": str(exc), "code": "timeout"}, 504)

    def cross_site_problem(self, expected_type="application/json"):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.split(";")[0].strip().lower() == expected_type:
            return "Unexpected request content type."
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return "Cross-site requests are not allowed."
        origin = self.headers.get("Origin")
        if origin:
            scheme = "https" if self.secure else "http"
            if origin != f"{scheme}://{self.headers.get('Host', '')}":
                return "Cross-origin requests are not allowed."
        return None

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Movie-World-Token")
        self.end_headers()

    def handle_tizen(self, path):
        token_path = ROOT / ".local" / "tizen-token"
        expected = token_path.read_text().strip() if token_path.exists() else ""
        supplied = self.headers.get("X-Movie-World-Token", "")
        if not expected or not re.fullmatch(r"[0-9]{6}", supplied) or not secrets.compare_digest(expected, supplied):
            self.send_json({"error": "Pair this app with the PC token."}, 401)
            return
        if self.tv is None:
            self.send_json({"error": "Playback service is disabled."}, 503)
            return
        body = self.read_json()
        if body is None:
            return
        from tvplay.errors import PlaybackError
        from tvplay.jobs import Busy, NotFound
        manager = self.tv.manager
        try:
            if path == "/api/tizen/prepare":
                movie_id = body.get("movie_id")
                movie = movie_detail(movie_id) if type(movie_id) is int else None
                if movie is None:
                    self.send_json({"error": "Movie not found"}, 404)
                    return
                job, created = self.tv.call(manager.create(movie, "tizen:" + self.client_address[0], body.get("idempotency_key"), False, body.get("start_at")))
                job = self.tv.call(manager.app_status(job["id"]))
                self.send_json({"job": job}, 202 if created else 200)
                return
            match = re.fullmatch(r"/api/tizen/([0-9a-f]{32})/(status|progress|cancel)", path)
            if not match:
                self.send_json({"error": "Not found"}, 404)
                return
            job_id, action = match.groups()
            current = self.tv.call(manager.get(job_id))
            if current["renderer"]["id"] != "tizen:" + self.client_address[0]:
                self.send_json({"error": "Session belongs to another device."}, 403)
                return
            if action == "progress":
                result = self.tv.call(manager.app_progress(job_id, body))
            elif action == "cancel":
                result = self.tv.call(manager.cancel(job_id))
            else:
                result = self.tv.call(manager.app_status(job_id))
            self.send_json({"job": result})
        except Busy:
            self.send_json({"error": "Another movie is active. Stop it first."}, 409)
        except NotFound:
            self.send_json({"error": "Session not found"}, 404)
        except PlaybackError as exc:
            self.send_json({"error": exc.message, "code": exc.code}, 400)

        except (TimeoutError, OSError) as exc:
            self.send_json({"error": "PC playback service unavailable. Try again.", "code": "unavailable"}, 503)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.send_json({"error": "Request too large", "code": "bad_request"}, 413)
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        if not isinstance(body, dict):
            self.send_json({"error": "Invalid JSON", "code": "bad_request"}, 400)
            return None
        return body

    def handle_api(self, path, query):
        parts = [part for part in path.split("/") if part]
        if path == "/api/tizen-catalogue/home":
            raw = parse_qs(query).get('day', ['1'])[0]
            self.send_json(tizen_home(int(raw) if raw.isascii() and raw.isdigit() and len(raw)<4 else 1))
            return
        if path == "/api/tizen-catalogue/search":
            self.send_json(tizen_search(parse_qs(query)))
            return
        if len(parts) == 4 and parts[:2] == ['api', 'movies'] and parts[3] == 'similar':
            movie_id = parse_id(parts[2])
            self.send_json(tizen_similar(movie_id) if movie_id is not None else [])
            return
        if parts == ["api", "genres"]:
            self.send_json(genres())
            return
        if parts == ["api", "search"]:
            params = parse_qs(query, keep_blank_values=True)
            self.send_json(search_movies(
                params.get("q", [""])[0],
                params.get("years", [""])[0],
            ))
            return
        if parts == ["api", "actors"]:
            params = parse_qs(query, keep_blank_values=True)
            self.send_json(search_actors(params.get("q", [""])[0]))
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "actors" and parts[3] == "movies":
            actor_id = parse_id(parts[2])
            if actor_id is None:
                self.send_json({"error": "Actor not found"}, 404)
                return
            payload = actor_movies(actor_id)
            if payload is None:
                self.send_json({"error": "Actor not found"}, 404)
                return
            self.send_json(payload)
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "genres" and parts[3] == "movies":
            genre_id = parse_id(parts[2])
            if genre_id is None:
                self.send_json({"error": "Genre not found"}, 404)
                return
            movies = genre_movies(genre_id)
            if movies is None:
                self.send_json({"error": "Genre not found"}, 404)
                return
            self.send_json(movies)
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "movies":
            movie_id = parse_id(parts[2])
            if movie_id is None:
                self.send_json({"error": "Movie not found"}, 404)
                return
            detail = movie_detail(movie_id)
            if detail is None:
                self.send_json({"error": "Movie not found"}, 404)
                return
            self.send_json(detail)
            return
        self.send_json({"error": "Not found"}, 404)

    def serve_static(self, path):
        file_path = static_file(path)
        if file_path is None:
            self.send_error(404)
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(file_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if not self.path.startswith("/api/personal"):
            self.send_header("Access-Control-Allow-Origin", "*")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def parse_id(value):
    if not value.isdigit():
        return None
    return int(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--cert", type=Path, help="HTTPS certificate in PEM format")
    parser.add_argument("--key", type=Path, help="HTTPS private key in PEM format")
    parser.add_argument("--tv-playback", action="store_true", help="Enable Play on TV (needs requirements.txt)")
    parser.add_argument("--media-port", type=int, help="Plain-HTTP port the TV fetches video from (default 8766)")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("--cert and --key must be supplied together")
    if not DB.is_file():
        raise SystemExit(f"Database not found: {DB}")
    if not (WEB / "index.html").is_file():
        raise SystemExit(f"Missing frontend: {WEB / 'index.html'}")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    scheme = "http"
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    Handler.secure = scheme == "https"
    tv = start_tv_playback(args) if args.tv_playback else None
    from tvplay.personal import PersonalStore
    from tvplay import config as personal_config
    Handler.personal = PersonalStore(personal_config.JOBS_DB)
    personal_runtime = None
    try:
        from tvplay.personal_sync import PersonalRuntime
        personal_runtime = PersonalRuntime(Handler.personal, personal_config.load(), tv)
        personal_runtime.start()
        Handler.personal_sync = personal_runtime
    except ImportError:
        print("Telegram sync unavailable; local personal storage enabled. Install requirements.txt.", flush=True)
    print(f"Movie World at {scheme}://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if personal_runtime is not None:
            personal_runtime.stop()
        if tv is not None:
            tv.stop()


def start_tv_playback(args):
    try:
        from tvplay import config as tv_config
        from tvplay.service import TvPlayback
    except ImportError as exc:
        print(f"Play on TV disabled: {exc}. Install it with: pip install -r requirements.txt", flush=True)
        return None
    settings = tv_config.load()
    if args.media_port:
        settings["media_port"] = args.media_port
    tv = TvPlayback(settings)
    try:
        tv.start()
    except OSError as exc:
        print(f"Play on TV disabled: media port {settings['media_port']} unavailable ({exc}).", flush=True)
        return None
    Handler.tv = tv
    print(f"Play on TV enabled. The TV fetches video on port {settings['media_port']}.", flush=True)
    return tv


if __name__ == "__main__":
    main()
