#!/usr/bin/env python3
"""Local genre browser for the Movie World SQLite catalogue. Stdlib only."""
import json
import argparse
import re
import ssl
import sqlite3
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
TV_ROUTES = (("api", "tv"), ("api", "tvs"), ("api", "playback"))


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
    secure = False

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
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
        if not is_tv_route(path):
            self.send_json({"error": "Not found"}, 404)
            return
        self.handle_tv("POST", path, parsed.query)

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
                if not valid_id or not isinstance(renderer_id, str) or len(renderer_id) > 200:
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

    def cross_site_problem(self):
        content_type = self.headers.get("Content-Type", "")
        if not content_type.split(";")[0].strip().lower() == "application/json":
            return "Expected a JSON request."
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return "Cross-site requests are not allowed."
        origin = self.headers.get("Origin")
        if origin:
            scheme = "https" if self.secure else "http"
            if origin != f"{scheme}://{self.headers.get('Host', '')}":
                return "Cross-origin requests are not allowed."
        return None

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
    print(f"Movie World at {scheme}://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
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
