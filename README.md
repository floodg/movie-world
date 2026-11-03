# Movie World

A local movie catalogue browser built with Python, SQLite, and vanilla HTML, CSS, and JavaScript.

## Features

- Browse genres with movies sorted by IMDb rating.
- Search the catalogue by title or actor.
- View movie details, posters, and backdrops.
- Save favourites in your browser's local storage.
- Enrich movie metadata and cast information using TMDB.

Titles tagged with the Adult genre are excluded from the browser.

## Run locally

Install Python 3. No third-party Python packages or frontend build tools are required.

From the project directory, run:

```sh
python server.py
```

Open http://127.0.0.1:8765 in your browser. Stop the server with Ctrl+C.

The server reads the included `sql-lite/movie-world.sqlite` catalogue in read-only mode and listens on the local computer only. Poster and backdrop images require internet access. Favourites are stored per browser and are not written to the database.

## Update catalogue metadata

The optional enrichment scripts update the database using the TMDB API. Back up the database before running them. Set `TMDB_API_KEY` in your shell; the scripts do not automatically load `.env`.

PowerShell:

```powershell
$env:TMDB_API_KEY = "your-tmdb-api-key"
python scripts/enrich_johns_movies.py sql-lite/movie-world.sqlite --limit 10
python scripts/enrich_cast.py sql-lite/movie-world.sqlite --limit 10
```

Omit `--limit` to process all eligible entries. Both scripts support `--delay` to control the pause between requests and `--help` for options. Cast enrichment uses TMDB IDs already stored by metadata enrichment. An API key is not needed to browse the existing catalogue.

Keep API keys private. The local `.env` file is excluded from Git.

## Project layout

- `server.py` — local HTTP server and catalogue API.
- `web/` — browser interface and styles.
- `sql-lite/movie-world.sqlite` — movie catalogue database.
- `scripts/` — TMDB metadata and cast enrichment tools.
- `docs/` — supporting project documents.

## Attribution

This product uses the TMDB API but is not endorsed or certified by TMDB.
