const FAVOURITES_KEY = "movie-world-favourites";
const YEAR_FILTER_KEY = "movie-world-year-filter";
const YEAR_MIN = 1880;
const YEAR_MAX = 2100;
const DECADES = [
  { start: 1930, label: "30s" },
  { start: 1940, label: "40s" },
  { start: 1950, label: "50s" },
  { start: 1960, label: "60s" },
  { start: 1970, label: "70s" },
  { start: 1980, label: "80s" },
  { start: 1990, label: "90s" },
  { start: 2000, label: "2000s" },
  { start: 2010, label: "2010s" },
  { start: 2020, label: "2020s" },
];
const genresEl = document.getElementById("genres");
const railEl = document.getElementById("rail");
const detailEl = document.getElementById("detail");
const favouritesEl = document.getElementById("favourites");
const favouritesCountEl = document.getElementById("favourites-count");
const searchForm = document.getElementById("search");
const searchInput = document.getElementById("title-search");
const actorForm = document.getElementById("actor-search");
const actorQuery = document.getElementById("actor-query");
const resultsEl = document.getElementById("results");

const movieCache = new Map();
let genres = [];
let openGenreId = null;
let favourites = loadFavourites();
let searchTimer = null;
let searchSeq = 0;
let searchController = null;
let actorTimer = null;
let actorSeq = 0;
let actorController = null;
let actorPicks = [];
let yearFilter = loadYearFilter();
let resultsMode = null;
let actorResults = null;

const MEDIA_LABELS = {
  tvMovie: "TV Movie",
  tvSeries: "Series",
  tvMiniSeries: "Mini-series",
  tvSpecial: "Special",
  short: "Short",
  video: "Video",
};

function hue(name) {
  let hash = 0;
  for (const char of name) hash = (hash * 33 + char.charCodeAt(0)) % 360;
  return hash;
}

function isTmdbImage(url) {
  return typeof url === "string" && url.startsWith("https://image.tmdb.org/");
}

function formatRating(rating) {
  return rating == null ? "" : Number(rating).toFixed(1);
}

function loadYearFilter() {
  try {
    const raw = JSON.parse(localStorage.getItem(YEAR_FILTER_KEY) || "null");
    const known = new Set(DECADES.map((decade) => decade.start));
    const decades = Array.isArray(raw?.decades)
      ? [...new Set(raw.decades.filter((start) => known.has(start)))].sort((a, b) => a - b)
      : [];
    const ranges = [];
    if (Array.isArray(raw?.ranges)) {
      for (const range of raw.ranges) {
        const parsed = normalizeYearRange(range?.from, range?.to);
        if (!parsed || ranges.some((item) => item.from === parsed.from && item.to === parsed.to)) continue;
        ranges.push(parsed);
      }
    }
    ranges.sort((a, b) => a.from - b.from || a.to - b.to);
    return { decades, ranges };
  } catch {
    return { decades: [], ranges: [] };
  }
}

function normalizeYearRange(from, to) {
  if (!Number.isInteger(from) || !Number.isInteger(to)) return null;
  if (from > to) [from, to] = [to, from];
  if (from < YEAR_MIN || to > YEAR_MAX) return null;
  return { from, to };
}

function parseYearField(value) {
  if (!/^\d{4}$/.test(value)) return null;
  const year = Number(value);
  if (year < YEAR_MIN || year > YEAR_MAX) return null;
  return year;
}

function hasYearFilter() {
  return yearFilter.decades.length > 0 || yearFilter.ranges.length > 0;
}

function rangeLabel(range) {
  return range.from === range.to ? String(range.from) : `${range.from}–${range.to}`;
}

function yearFilterParts() {
  const parts = [];
  for (const decade of DECADES) {
    if (yearFilter.decades.includes(decade.start)) parts.push(decade.label);
  }
  for (const range of yearFilter.ranges) parts.push(rangeLabel(range));
  return parts;
}

function yearFilterLabel() {
  return yearFilterParts().join(", ");
}

function yearFilterSummary() {
  const parts = yearFilterParts();
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0];
  return `${parts[0]} +${parts.length - 1}`;
}

function yearQueryParam() {
  const parts = yearFilter.decades.map((start) => `${start}-${start + 9}`);
  for (const range of yearFilter.ranges) {
    parts.push(range.from === range.to ? String(range.from) : `${range.from}-${range.to}`);
  }
  return parts.join(",");
}

function movieMatchesYear(movie) {
  if (!hasYearFilter()) return true;
  if (movie.year == null || movie.year === "") return false;
  const year = Number(movie.year);
  if (!Number.isInteger(year)) return false;
  if (yearFilter.decades.some((start) => year >= start && year <= start + 9)) return true;
  return yearFilter.ranges.some((range) => year >= range.from && year <= range.to);
}

function saveYearFilter() {
  localStorage.setItem(YEAR_FILTER_KEY, JSON.stringify(yearFilter));
}

function formatRuntime(minutes) {
  if (minutes == null) return "";
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours && mins) return `${hours}h ${mins}m`;
  if (hours) return `${hours}h`;
  return `${mins}m`;
}

async function fetchJson(url, signal) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function renderGenres() {
  genresEl.replaceChildren();
  for (const genre of genres) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "genre-tile" + (genre.id === openGenreId ? " is-open" : "");
    button.style.background = `linear-gradient(145deg, hsl(${hue(genre.name)} 42% 32%), #141414)`;
    button.setAttribute("aria-expanded", genre.id === openGenreId ? "true" : "false");
    button.setAttribute("aria-controls", "rail");
    button.addEventListener("click", () => toggleGenre(genre));

    if (isTmdbImage(genre.backdrop_url)) {
      const image = document.createElement("img");
      image.alt = "";
      image.src = genre.backdrop_url;
      image.addEventListener("error", () => image.remove());
      button.appendChild(image);
    }

    const shade = document.createElement("span");
    shade.className = "shade";
    const copy = document.createElement("span");
    copy.className = "copy";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = genre.name;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${genre.movie_count.toLocaleString()} titles`;
    copy.append(name, count);
    button.append(shade, copy);
    genresEl.appendChild(button);
  }
}

async function toggleGenre(genre) {
  if (openGenreId === genre.id) {
    openGenreId = null;
    railEl.hidden = true;
    railEl.replaceChildren();
    renderGenres();
    return;
  }
  openGenreId = genre.id;
  renderGenres();
  railEl.hidden = false;
  railEl.replaceChildren();
  const status = document.createElement("p");
  status.className = "rail-status";
  status.textContent = `Loading ${genre.name}…`;
  railEl.appendChild(status);
  try {
    const movies = await loadMovies(genre.id);
    if (openGenreId !== genre.id) return;
    renderRail(genre, movies);
  } catch (error) {
    if (openGenreId !== genre.id) return;
    status.textContent = "Could not load this genre. Try it again.";
    scrollToMovieList();
  }
}

function scrollToMovieList() {
  requestAnimationFrame(() => {
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: "smooth",
    });
  });
}

async function loadMovies(genreId) {
  if (!movieCache.has(genreId)) {
    const pending = fetchJson(`/api/genres/${genreId}/movies`).catch((error) => {
      movieCache.delete(genreId);
      throw error;
    });
    movieCache.set(genreId, pending);
  }
  return movieCache.get(genreId);
}

function renderRail(genre, movies, scroll = true) {
  railEl.replaceChildren();
  const shown = movies.filter(movieMatchesYear);
  const bar = document.createElement("div");
  bar.className = "rail-bar";
  const headingWrap = document.createElement("div");
  headingWrap.className = "rail-heading";
  const heading = document.createElement("h2");
  heading.textContent = genre.name;
  headingWrap.appendChild(heading);
  if (hasYearFilter()) {
    const note = document.createElement("p");
    note.className = "rail-filter";
    note.textContent = shown.length
      ? `${shown.length.toLocaleString()} · ${yearFilterLabel()}`
      : `No titles in ${yearFilterLabel()}`;
    headingWrap.appendChild(note);
  }
  appendMovieRail(railEl, bar, headingWrap, shown, `No ${genre.name} titles in ${yearFilterLabel()}.`);
  if (scroll) scrollToMovieList();
}

function appendMovieRail(section, bar, headingWrap, shown, emptyMessage) {
  const nav = document.createElement("div");
  nav.className = "rail-nav";
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = "‹";
  prev.setAttribute("aria-label", "Scroll left");
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = "›";
  next.setAttribute("aria-label", "Scroll right");
  nav.append(prev, next);
  bar.append(headingWrap, nav);

  if (!shown.length) {
    prev.disabled = true;
    next.disabled = true;
    const status = document.createElement("p");
    status.className = "rail-status";
    status.textContent = emptyMessage;
    section.append(bar, status);
    return;
  }

  const scroller = document.createElement("div");
  scroller.className = "scroller";
  enableMouseDrag(scroller);
  for (const movie of shown) scroller.appendChild(movieCard(movie));

  prev.addEventListener("click", () => {
    scroller.scrollBy({ left: -scroller.clientWidth * 0.8, behavior: "smooth" });
  });
  next.addEventListener("click", () => {
    scroller.scrollBy({ left: scroller.clientWidth * 0.8, behavior: "smooth" });
  });
  const syncNav = () => {
    prev.disabled = scroller.scrollLeft <= 4;
    next.disabled = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 4;
  };
  scroller.addEventListener("scroll", syncNav);
  section.append(bar, scroller);
  requestAnimationFrame(syncNav);
}

function enableMouseDrag(scroller) {
  let drag = null;
  let suppressClick = false;
  scroller.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    // Leave the native scrollbar available for dragging too.
    if (event.clientY >= scroller.getBoundingClientRect().top + scroller.clientHeight) return;
    suppressClick = false;
    drag = { id: event.pointerId, x: event.clientX, left: scroller.scrollLeft, active: false };
  });
  scroller.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const distance = event.clientX - drag.x;
    if (!drag.active) {
      if (Math.abs(distance) < 6) return;
      drag.active = true;
      suppressClick = true;
      scroller.setPointerCapture(event.pointerId);
      scroller.classList.add("is-dragging");
    }
    event.preventDefault();
    scroller.scrollLeft = drag.left - distance;
  });
  const finish = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    drag = null;
    scroller.classList.remove("is-dragging");
    if (scroller.hasPointerCapture(event.pointerId)) scroller.releasePointerCapture(event.pointerId);
  };
  scroller.addEventListener("pointerup", finish);
  scroller.addEventListener("pointercancel", finish);
  scroller.addEventListener("lostpointercapture", finish);
  scroller.addEventListener("pointerleave", (event) => {
    if (drag && !drag.active) finish(event);
  });
  scroller.addEventListener("dragstart", (event) => event.preventDefault());
  scroller.addEventListener("click", (event) => {
    if (!suppressClick || event.detail === 0) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    suppressClick = false;
  }, true);
}

let watchedProgress = new Map();
let watchedProgressRequest = 0;
let continueWatching = [];
let historyRequest = 0;
/** @type {Map<string, {movie_id:number, position:number, duration:number|null, finished:boolean, updated_at:number}>} */
let watchStore = new Map();
const continueWatchingEl = document.getElementById("continue-watching");
const continueWatchingRow = document.getElementById("continue-watching-row");
const historyEl = document.getElementById("history");
const historyMoviesEl = document.getElementById("history-movies");
const historyStatusEl = document.getElementById("history-status");
enableMouseDrag(continueWatchingRow);
const WATCH_LOCAL_KEY = "movie-world-watch-progress";

function loadLocalWatchStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(WATCH_LOCAL_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((item) => item && Number.isInteger(Number(item.movie_id)));
  } catch {
    return [];
  }
}

function saveLocalWatchStore() {
  localStorage.setItem(WATCH_LOCAL_KEY, JSON.stringify([...watchStore.values()]));
}

function watchUpdatedAt(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const ms = Date.parse(value);
    if (Number.isFinite(ms)) return ms / 1000;
  }
  return 0;
}

function isContinuePoint(point) {
  const position = Number(point.position) || 0;
  const duration = Number(point.duration) || 0;
  if (point.finished) return false;
  if (position < 15) return false;
  if (duration > 0 && position >= duration - 30) return false;
  return true;
}

function applyWatchStoreToUi() {
  watchedProgress = new Map();
  for (const point of watchStore.values()) {
    if (!point.duration || point.duration <= 0 || point.finished) continue;
    watchedProgress.set(String(point.movie_id), Math.min(100, 100 * point.position / point.duration));
  }
  for (const line of document.querySelectorAll(".watched-progress")) updateWatchedProgress(line);
}

function renderContinueWatching() {
  const shown = continueWatching.filter(movieMatchesYear);
  continueWatchingRow.replaceChildren(...shown.map(movieCard));
  continueWatchingEl.hidden = favouritesEl.hidden || !shown.length;
}

async function renderHistory() {
  if (!historyEl.open) return;
  const request = ++historyRequest;
  const points = [...watchStore.values()].sort((a, b) => b.updated_at - a.updated_at);
  const movies = await cardsForPoints(points);
  if (request !== historyRequest || !historyEl.open) return;
  const byId = new Map(movies.map((movie) => [String(movie.id), movie]));
  const cards = points.flatMap((point) => {
    const movie = byId.get(String(point.movie_id));
    if (!movie) return [];
    const card = movieCard(movie);
    const note = document.createElement("p");
    note.className = "rail-status";
    const date = new Date(point.updated_at * 1000).toLocaleString();
    note.textContent = `${point.finished ? "Watched" : "In progress"} · ${date}`;
    card.appendChild(note);
    return [card];
  });
  historyMoviesEl.replaceChildren(...cards);
  historyStatusEl.textContent = points.length
    ? (cards.length ? "Latest watched first. " : "History titles are unavailable from this catalogue. ")
    : "Your watch history is empty. ";
  historyStatusEl.textContent += window.MovieWorldCloud?.session()
    ? "History syncs with your account."
    : "Saved on this browser. Sign in to sync history across devices.";
}

document.getElementById("history-open").addEventListener("click", async () => {
  historyEl.showModal();
  historyStatusEl.textContent = "Loading history…";
  await refreshWatchedProgress();
  if (window.MovieWorldCloud?.session()) {
    historyStatusEl.textContent = "Syncing history…";
    try {
      await window.MovieWorldCloud.syncWatchProgress();
      await renderHistory();
    } catch {
      await renderHistory();
      historyStatusEl.textContent += " Could not sync; showing the saved copy.";
    }
  }
});
document.getElementById("history-close").addEventListener("click", () => historyEl.close());

async function cardsForPoints(points, movies) {
  if (movies && movies.length) return movies;
  const results = await Promise.allSettled(
    points.map(({ movie_id }) => fetchJson(`/api/movies/${movie_id}`))
  );
  return results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
}

async function refreshWatchRails() {
  const points = [...watchStore.values()].sort((a, b) => b.updated_at - a.updated_at);
  const continuingPoints = points.filter(isContinuePoint).slice(0, 20);
  const continueCards = await cardsForPoints(continuingPoints);
  const continueIds = new Set(continuingPoints.map((point) => String(point.movie_id)));
  continueWatching = continueCards.filter((movie) => continueIds.has(String(movie.id)));
  applyWatchStoreToUi();
  renderContinueWatching();
  await renderHistory();
}

function upsertWatchPoint(point, { mirror = false, pushCloud = false } = {}) {
  const movieId = Number(point.movie_id);
  if (!Number.isInteger(movieId) || movieId <= 0) return;
  const next = {
    movie_id: movieId,
    position: Math.max(0, Math.floor(Number(point.position) || 0)),
    duration: point.duration == null ? null : Math.max(0, Math.floor(Number(point.duration) || 0)),
    finished: Boolean(point.finished),
    updated_at: watchUpdatedAt(point.updated_at) || Date.now() / 1000,
  };
  const existing = watchStore.get(String(movieId));
  if (existing && existing.updated_at > next.updated_at) return existing;
  if (next.finished || (next.duration > 0 && next.position >= Math.min(next.duration - 90, next.duration * 0.95))) {
    next.finished = true;
  }
  watchStore.set(String(movieId), next);
  saveLocalWatchStore();
  if (pushCloud && window.MovieWorldCloud) {
    window.MovieWorldCloud.pushWatchProgress({
      movie_id: next.movie_id,
      position_seconds: next.position,
      duration_seconds: next.duration,
      finished: next.finished,
      updated_at: new Date(next.updated_at * 1000).toISOString(),
    });
  }
  if (mirror) mirrorResumeToLan([next]);
  return next;
}

async function mirrorResumeToLan(points) {
  try {
    const response = await fetch("/api/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        points: points.map((point) => ({
          movie_id: point.movie_id,
          position: point.position,
          duration: point.duration,
          updated_at: point.updated_at,
          finished: point.finished,
          clear: point.finished,
        })),
      }),
    });
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
  } catch {
    // TV playback may be disabled on this server.
  }
}

async function refreshWatchedProgress() {
  const request = ++watchedProgressRequest;
  for (const item of loadLocalWatchStore()) upsertWatchPoint(item);
  try {
    const { points, movies } = await fetchJson("/api/resume");
    if (request !== watchedProgressRequest) return;
    const winners = [];
    for (const point of points) {
      const next = upsertWatchPoint({
        movie_id: point.movie_id,
        position: point.position,
        duration: point.duration,
        finished: false,
        updated_at: point.updated_at ?? Date.now() / 1000,
      }, { pushCloud: true });
      if (next) winners.push(next);
    }
    if (movies && movies.length) {
      const ids = new Set(points.map((point) => String(point.movie_id)));
      continueWatching = movies.filter((movie) => ids.has(String(movie.id)));
    }
  } catch {
    // The catalogue can run without TV playback enabled.
  }
  if (request !== watchedProgressRequest) return;
  await refreshWatchRails();
}

function updateWatchedProgress(line) {
  const percent = watchedProgress.get(line.dataset.movieId) || 0;
  line.style.width = `${percent}%`;
  line.hidden = percent <= 0;
}

window.addEventListener("movie-world-progress", (event) => {
  const detail = event.detail;
  if (detail && detail.movie_id != null) {
    upsertWatchPoint({
      movie_id: detail.movie_id,
      position: detail.position,
      duration: detail.duration,
      finished: detail.finished,
      updated_at: Date.now() / 1000,
    }, { mirror: true, pushCloud: true });
    refreshWatchRails();
    return;
  }
  refreshWatchedProgress();
});

window.MovieWorldSync = {
  exportFavouritesForCloud() {
    return loadFavouriteStore().map((item) => ({
      movie_id: item.id,
      removed_at: item.removed_at,
      updated_at: item.updated_at,
      title: item.title,
      year: item.year,
      rating: item.rating,
      poster_url: item.poster_url,
      media_type: item.media_type,
    }));
  },
  async mergeFavouritesFromCloud(rows) {
    const store = loadFavouriteStore();
    const byId = new Map(store.map((item) => [item.id, item]));
    for (const row of rows || []) {
      const id = Number(row.movie_id);
      if (!Number.isInteger(id) || id <= 0) continue;
      const incomingAt = watchUpdatedAt(row.updated_at);
      const existing = byId.get(id);
      if (existing && watchUpdatedAt(existing.updated_at) > incomingAt) continue;
      byId.set(id, {
        id,
        title: (existing && existing.title) || `Movie ${id}`,
        year: existing && existing.year != null ? existing.year : null,
        rating: existing && existing.rating != null ? existing.rating : null,
        poster_url: existing && existing.poster_url != null ? existing.poster_url : null,
        media_type: existing && existing.media_type != null ? existing.media_type : null,
        updated_at: typeof row.updated_at === "string" ? row.updated_at : new Date(incomingAt * 1000).toISOString(),
        removed_at: row.removed_at
          ? (typeof row.removed_at === "string" ? row.removed_at : new Date(watchUpdatedAt(row.removed_at) * 1000).toISOString())
          : null,
      });
    }
    const next = [...byId.values()];
    saveFavouriteStore(next);
    favourites = next.filter((item) => !item.removed_at);
    // Fill titles for cloud-only ids from the catalogue.
    const missing = favourites.filter((item) => item.title.startsWith("Movie "));
    if (missing.length) {
      const results = await Promise.allSettled(missing.map((item) => fetchJson(`/api/movies/${item.id}`)));
      for (const result of results) {
        if (result.status !== "fulfilled") continue;
        const movie = result.value;
        const index = favourites.findIndex((item) => item.id === movie.id);
        if (index >= 0) favourites[index] = { ...favourites[index], ...favouriteSnapshot(movie), removed_at: null };
      }
      saveFavourites();
    }
    renderFavourites();
    syncFavButtons();
  },
  exportWatchForCloud() {
    return [...watchStore.values()].map((point) => ({
      movie_id: point.movie_id,
      position_seconds: point.position,
      duration_seconds: point.duration,
      finished: point.finished,
      updated_at: new Date(point.updated_at * 1000).toISOString(),
    }));
  },
  async mergeWatchFromCloud(rows) {
    const mirrored = [];
    for (const row of rows || []) {
      const next = upsertWatchPoint({
        movie_id: row.movie_id,
        position: row.position_seconds,
        duration: row.duration_seconds,
        finished: row.finished,
        updated_at: row.updated_at,
      });
      if (next) mirrored.push(next);
    }
    if (mirrored.length) await mirrorResumeToLan(mirrored);
    await refreshWatchRails();
  },
};

function movieCard(movie) {
  const card = document.createElement("article");
  card.className = "card";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "card-open";
  button.addEventListener("click", () => openDetail(movie.id));

  const wrap = document.createElement("span");
  wrap.className = "poster-wrap";
  if (isTmdbImage(movie.poster_url)) {
    const image = document.createElement("img");
    image.alt = `${movie.title} poster`;
    image.loading = "lazy";
    image.src = movie.poster_url;
    image.addEventListener("error", () => {
      image.replaceWith(placeholder());
    });
    wrap.appendChild(image);
  } else {
    wrap.appendChild(placeholder());
  }
  const line = document.createElement("span");
  line.className = "watched-progress";
  line.dataset.movieId = movie.id;
  line.setAttribute("aria-hidden", "true");
  updateWatchedProgress(line);
  wrap.appendChild(line);

  const rating = formatRating(movie.rating);
  if (rating) {
    const badge = document.createElement("span");
    badge.className = "rating-badge";
    badge.textContent = rating;
    wrap.appendChild(badge);
  }
  const media = MEDIA_LABELS[movie.media_type];
  if (media) {
    const badge = document.createElement("span");
    badge.className = "media-badge";
    badge.textContent = media;
    wrap.appendChild(badge);
  }

  const title = document.createElement("p");
  title.className = "card-title";
  title.textContent = movie.title;
  const meta = document.createElement("p");
  meta.className = "card-meta";
  meta.textContent = movie.year ? String(movie.year) : "";
  button.append(wrap, title, meta);
  card.append(button, favToggle(movie));
  return card;
}

function placeholder() {
  const span = document.createElement("span");
  span.className = "placeholder";
  return span;
}

function heartIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute(
    "d",
    "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"
  );
  svg.appendChild(path);
  return svg;
}

function loadFavouriteStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(FAVOURITES_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    const seen = new Set();
    const items = [];
    for (const item of raw) {
      const movie = normalizeFavourite(item);
      if (!movie || seen.has(movie.id)) continue;
      seen.add(movie.id);
      items.push(movie);
    }
    return items;
  } catch {
    return [];
  }
}

function loadFavourites() {
  return loadFavouriteStore().filter((item) => !item.removed_at);
}

function normalizeFavourite(item) {
  if (!item || typeof item !== "object") return null;
  const id = Number(item.id ?? item.movie_id);
  if (!Number.isInteger(id) || id <= 0) return null;
  const title = typeof item.title === "string" ? item.title : `Movie ${id}`;
  const updatedAt = typeof item.updated_at === "string"
    ? item.updated_at
    : (typeof item.updated_at === "number"
      ? new Date(item.updated_at * 1000).toISOString()
      : new Date(0).toISOString());
  const removedAt = item.removed_at
    ? (typeof item.removed_at === "string" ? item.removed_at : new Date(item.removed_at * 1000).toISOString())
    : null;
  return {
    id,
    title,
    year: Number.isInteger(item.year) ? item.year : null,
    rating: typeof item.rating === "number" ? item.rating : null,
    poster_url: typeof item.poster_url === "string" ? item.poster_url : null,
    media_type: typeof item.media_type === "string" ? item.media_type : null,
    updated_at: updatedAt,
    removed_at: removedAt,
  };
}

function saveFavouriteStore(store) {
  localStorage.setItem(FAVOURITES_KEY, JSON.stringify(store));
}

function saveFavourites() {
  const store = loadFavouriteStore();
  const activeIds = new Set(favourites.map((movie) => movie.id));
  const next = [];
  const seen = new Set();
  for (const movie of favourites) {
    next.push({ ...movie, removed_at: null, updated_at: movie.updated_at || new Date().toISOString() });
    seen.add(movie.id);
  }
  for (const item of store) {
    if (seen.has(item.id)) continue;
    if (!activeIds.has(item.id) && item.removed_at) {
      next.push(item);
      seen.add(item.id);
    }
  }
  saveFavouriteStore(next);
}

function isFavourite(id) {
  return favourites.some((movie) => movie.id === id);
}

function favouriteSnapshot(movie) {
  return {
    id: movie.id,
    title: movie.title,
    year: movie.year ?? null,
    rating: movie.rating ?? null,
    poster_url: movie.poster_url ?? null,
    media_type: movie.media_type ?? null,
    updated_at: new Date().toISOString(),
    removed_at: null,
  };
}

function toggleFavourite(movie) {
  const now = new Date().toISOString();
  const store = loadFavouriteStore();
  if (isFavourite(movie.id)) {
    favourites = favourites.filter((item) => item.id !== movie.id);
    const existing = store.find((item) => item.id === movie.id);
    const tombstone = {
      ...(existing || favouriteSnapshot(movie)),
      id: movie.id,
      updated_at: now,
      removed_at: now,
    };
    saveFavouriteStore([tombstone, ...store.filter((item) => item.id !== movie.id)]);
    if (window.MovieWorldCloud) window.MovieWorldCloud.pushFavourite(movie.id, now, now);
  } else {
    const snapshot = favouriteSnapshot(movie);
    favourites = [snapshot, ...favourites];
    saveFavouriteStore([snapshot, ...store.filter((item) => item.id !== movie.id)]);
    if (window.MovieWorldCloud) window.MovieWorldCloud.pushFavourite(movie.id, null, snapshot.updated_at);
  }
  renderFavourites();
  syncFavButtons();
}

function applyFavState(button) {
  const on = isFavourite(Number(button.dataset.id));
  const title = button.dataset.title || "this title";
  button.classList.toggle("is-on", on);
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.setAttribute(
    "aria-label",
    on ? `Remove ${title} from favourites` : `Add ${title} to favourites`
  );
  const label = button.querySelector(".fav-label");
  if (label) label.textContent = on ? "In favourites" : "Add to favourites";
}

function syncFavButtons() {
  for (const button of document.querySelectorAll(".fav-toggle, .fav-action")) {
    applyFavState(button);
  }
}

function favToggle(movie) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "fav-toggle";
  button.dataset.id = String(movie.id);
  button.dataset.title = movie.title;
  button.appendChild(heartIcon());
  applyFavState(button);
  button.addEventListener("click", () => toggleFavourite(movie));
  return button;
}

function renderFavourites() {
  favouritesEl.replaceChildren();
  favouritesCountEl.textContent = String(favourites.length);
  const shown = favourites.filter(movieMatchesYear);
  const bar = document.createElement("div");
  bar.className = "rail-bar";
  const headingWrap = document.createElement("div");
  headingWrap.className = "rail-heading";
  const heading = document.createElement("h2");
  heading.id = "favourites-heading";
  heading.textContent = "Favourites";
  headingWrap.appendChild(heading);
  if (hasYearFilter() && favourites.length) {
    const note = document.createElement("p");
    note.className = "rail-filter";
    note.textContent = shown.length
      ? `${shown.length.toLocaleString()} in ${yearFilterLabel()}`
      : `None in ${yearFilterLabel()}`;
    headingWrap.appendChild(note);
  }
  const emptyMessage = favourites.length
    ? `No favourites in ${yearFilterLabel()}.`
    : "Save a title with the heart and it stays in this list.";
  appendMovieRail(favouritesEl, bar, headingWrap, shown, emptyMessage);
}

function clearDetailFit() {
  detailEl.style.maxHeight = "";
  detailEl.style.top = "";
  detailEl.style.bottom = "";
  detailEl.style.height = "";
  detailEl.style.marginTop = "";
  detailEl.style.marginBottom = "";
}

function fitDetailDialog() {
  if (!detailEl.open) return;
  const viewport = window.visualViewport;
  const narrow = window.matchMedia("(max-width: 640px)").matches;
  if (!narrow || !viewport) {
    clearDetailFit();
    return;
  }
  const gap = 8;
  detailEl.style.top = Math.floor(viewport.offsetTop + gap) + "px";
  detailEl.style.bottom = "auto";
  detailEl.style.height = "auto";
  detailEl.style.marginTop = "0";
  detailEl.style.marginBottom = "0";
  detailEl.style.maxHeight = Math.floor(viewport.height - gap * 2) + "px";
}

async function openDetail(movieId) {
  if (yearFilterPanel && !yearFilterPanel.hidden) {
    yearFilterPanel.hidden = true;
    yearFilterButton.setAttribute("aria-expanded", "false");
  }
  detailEl.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "detail-body";
  const message = document.createElement("p");
  message.textContent = "Loading…";
  loading.appendChild(message);
  detailEl.appendChild(loading);
  if (!detailEl.open) detailEl.showModal();
  fitDetailDialog();
  try {
    renderDetail(await fetchJson(`/api/movies/${movieId}`));
  } catch (error) {
    message.textContent = "Could not load this title.";
  }
}

function renderDetail(movie) {
  detailEl.replaceChildren();
  const hero = document.createElement("div");
  hero.className = "detail-hero";
  if (isTmdbImage(movie.backdrop_url)) {
    hero.style.backgroundImage = `url("${movie.backdrop_url}")`;
  }
  const shade = document.createElement("div");
  shade.className = "shade";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "close-detail";
  close.textContent = "×";
  close.setAttribute("aria-label", "Close");
  close.addEventListener("click", () => detailEl.close());
  hero.append(shade, close);

  const body = document.createElement("div");
  body.className = "detail-body";
  const heading = document.createElement("h2");
  heading.textContent = movie.year ? `${movie.title} (${movie.year})` : movie.title;
  const meta = document.createElement("div");
  meta.className = "detail-meta";
  const rating = formatRating(movie.rating);
  if (rating) {
    const score = document.createElement("span");
    score.className = "score";
    const votes = movie.votes == null ? "" : ` · ${Number(movie.votes).toLocaleString()} votes`;
    score.textContent = `IMDb ${rating}${votes}`;
    meta.appendChild(score);
  }
  const runtime = formatRuntime(movie.runtime_minutes);
  if (runtime) meta.appendChild(textSpan(runtime));
  if (movie.genres) meta.appendChild(textSpan(movie.genres));
  const media = MEDIA_LABELS[movie.media_type];
  if (media) meta.appendChild(textSpan(media));

  body.append(heading, meta);
  if (movie.cast && movie.cast.length) {
    const cast = document.createElement("p");
    cast.className = "cast";
    cast.append("Cast ");
    movie.cast.forEach((person, index) => {
      if (index) cast.append(", ");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "cast-link";
      button.textContent = person.name;
      button.addEventListener("click", () => {
        detailEl.close();
        loadActorMovies(person);
      });
      cast.appendChild(button);
    });
    body.appendChild(cast);
  }
  if (movie.overview) {
    const overview = document.createElement("p");
    overview.textContent = movie.overview;
    body.appendChild(overview);
  }
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const fav = document.createElement("button");
  fav.type = "button";
  fav.className = "fav-action";
  fav.dataset.id = String(movie.id);
  fav.dataset.title = movie.title;
  const favLabel = document.createElement("span");
  favLabel.className = "fav-label";
  fav.append(heartIcon(), favLabel);
  applyFavState(fav);
  fav.addEventListener("click", () => toggleFavourite(movie));
  actions.appendChild(fav);
  if (typeof movie.telegram_url === "string" && movie.telegram_url.startsWith("https://")) {
    const link = document.createElement("a");
    link.className = "telegram";
    link.href = movie.telegram_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Open in Telegram";
    actions.appendChild(link);
    const upload = document.createElement("button");
    upload.type = "button";
    upload.className = "fav-action";
    upload.textContent = "Upload from Telegram";
    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "video/*,.mkv,.avi,.m4v,.ts,.m2ts,.wmv,.mpg,.mpeg,.flv";
    picker.hidden = true;
    const status = document.createElement("p");
    status.className = "upload-status";
    status.setAttribute("role", "status");
    status.textContent = "Save the video from Telegram, then choose it here to upload to the server.";
    upload.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => {
      const file = picker.files[0];
      if (!file || upload.disabled) return;
      upload.disabled = true;
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/movies/${movie.id}/upload`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const percent = Math.floor(event.loaded / event.total * 100);
          status.textContent = percent === 100 ? "Upload received. Checking the video..." : `Uploading ${file.name}: ${percent}%`;
        }
      });
      xhr.addEventListener("load", () => {
        let result = {};
        try { result = JSON.parse(xhr.responseText); } catch {}
        status.textContent = xhr.status >= 200 && xhr.status < 300
          ? "Saved to the server cache. Choose Play on TV to stream it."
          : result.error || "Upload failed. Please try again.";
      });
      xhr.addEventListener("error", () => { status.textContent = "Connection lost. Select the file to try again."; });
      xhr.addEventListener("loadend", () => { upload.disabled = false; picker.value = ""; });
      status.textContent = `Uploading ${file.name}...`;
      xhr.send(file);
    });
    actions.append(upload, picker);
    body.appendChild(status);
  }
  body.appendChild(actions);
  detailEl.append(hero, body);
  fitDetailDialog();
  if (window.MovieWorldTv) window.MovieWorldTv.mount(actions, movie);
}

function textSpan(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span;
}

function showBrowse() {
  resultsMode = null;
  actorResults = null;
  resultsEl.hidden = true;
  resultsEl.replaceChildren();
  favouritesEl.hidden = false;
  continueWatchingEl.hidden = !continueWatchingRow.childElementCount;
  genresEl.hidden = false;
  railEl.hidden = openGenreId == null;
}

function hideBrowse() {
  favouritesEl.hidden = true;
  continueWatchingEl.hidden = true;
  genresEl.hidden = true;
  railEl.hidden = true;
  resultsEl.hidden = false;
}

function clearTitleSearch() {
  searchSeq += 1;
  if (searchController) searchController.abort();
}

function clearActorSearch() {
  actorSeq += 1;
  if (actorController) actorController.abort();
  actorPicks = [];
}

function clearSearch() {
  clearTitleSearch();
  clearActorSearch();
  showBrowse();
}

function showSearchPending(query, scroll = true) {
  resultsMode = "search";
  hideBrowse();
  resultsEl.replaceChildren();
  const status = document.createElement("p");
  status.className = "rail-status";
  status.textContent = `Searching for “${query}”…`;
  resultsEl.appendChild(status);
  if (scroll) resultsEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderResults(query, payload, kind) {
  resultsMode = kind === "actor" ? "actor" : "search";
  if (kind === "actor") actorResults = { query, payload };
  resultsEl.replaceChildren();
  const movies = payload.movies.filter(movieMatchesYear);
  const total = kind === "search" ? payload.total : movies.length;
  const bar = document.createElement("div");
  bar.className = "results-bar";
  const heading = document.createElement("h2");
  const note = document.createElement("p");
  note.className = "results-query";
  const yearNote = hasYearFilter() ? ` · ${yearFilterLabel()}` : "";
  if (!movies.length) {
    heading.textContent = "No titles match";
    note.textContent = hasYearFilter()
      ? (kind === "actor"
        ? `No titles with ${query} in ${yearFilterLabel()}.`
        : `Nothing matches “${query}” in ${yearFilterLabel()}.`)
      : `Nothing matches “${query}”.`;
    bar.append(heading, note);
    resultsEl.appendChild(bar);
    return;
  }
  const shown = movies.length === total
    ? `${total.toLocaleString()} ${total === 1 ? "title" : "titles"}`
    : `${movies.length} of ${total.toLocaleString()}`;
  heading.textContent = shown;
  note.textContent = (kind === "actor" ? `With ${query}` : `Matching “${query}”`) + yearNote;
  bar.append(heading, note);
  const grid = document.createElement("div");
  grid.className = "results-grid";
  for (const movie of movies) grid.appendChild(movieCard(movie));
  resultsEl.append(bar, grid);
}

async function runSearch(query, options = {}) {
  const seq = ++searchSeq;
  if (searchController) searchController.abort();
  searchController = new AbortController();
  showSearchPending(query, options.scroll !== false);
  try {
    const years = yearQueryParam();
    const yearsParam = years ? `&years=${encodeURIComponent(years)}` : "";
    const payload = await fetchJson(
      `/api/search?q=${encodeURIComponent(query)}${yearsParam}`,
      searchController.signal,
    );
    if (seq !== searchSeq) return;
    renderResults(query, payload);
  } catch (error) {
    if (error.name === "AbortError" || seq !== searchSeq) return;
    resultsEl.replaceChildren();
    const status = document.createElement("p");
    status.className = "rail-status";
    status.textContent = "Could not search titles. Try again.";
    resultsEl.appendChild(status);
  }
}

function renderActorPicks(query, actors) {
  resultsMode = "actors";
  actorResults = null;
  actorPicks = actors;
  resultsEl.replaceChildren();
  const bar = document.createElement("div");
  bar.className = "results-bar";
  const heading = document.createElement("h2");
  const note = document.createElement("p");
  note.className = "results-query";
  note.textContent = `Matching “${query}”`;
  if (!actors.length) {
    heading.textContent = "No actors match";
    bar.append(heading, note);
    resultsEl.appendChild(bar);
    return;
  }
  heading.textContent = actors.length === 1 ? "1 actor" : `${actors.length} actors`;
  bar.append(heading, note);
  const list = document.createElement("div");
  list.className = "actor-list";
  for (const actor of actors) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "actor-pick";
    const name = document.createElement("strong");
    name.textContent = actor.name;
    const count = document.createElement("span");
    const total = actor.movie_count;
    count.textContent = `${total.toLocaleString()} ${total === 1 ? "title" : "titles"}`;
    button.append(name, count);
    button.addEventListener("click", () => loadActorMovies(actor));
    list.appendChild(button);
  }
  resultsEl.append(bar, list);
}

function showActorHint() {
  resultsMode = "actors";
  actorResults = null;
  hideBrowse();
  clearTitleSearch();
  searchInput.value = "";
  resultsEl.replaceChildren();
  const status = document.createElement("p");
  status.className = "rail-status";
  status.textContent = "Type at least two letters of an actor’s name.";
  resultsEl.appendChild(status);
}

async function runActorSearch(query) {
  const seq = ++actorSeq;
  if (actorController) actorController.abort();
  actorController = new AbortController();
  hideBrowse();
  resultsEl.replaceChildren();
  const status = document.createElement("p");
  status.className = "rail-status";
  status.textContent = `Searching actors for “${query}”…`;
  resultsEl.appendChild(status);
  try {
    const payload = await fetchJson(
      `/api/actors?q=${encodeURIComponent(query)}`,
      actorController.signal,
    );
    if (seq !== actorSeq) return;
    renderActorPicks(query, payload.actors);
  } catch (error) {
    if (error.name === "AbortError" || seq !== actorSeq) return;
    resultsEl.replaceChildren();
    const failed = document.createElement("p");
    failed.className = "rail-status";
    failed.textContent = "Could not search actors. Try again.";
    resultsEl.appendChild(failed);
  }
}

async function loadActorMovies(actor) {
  resultsMode = "actor-loading";
  const seq = ++actorSeq;
  if (actorController) actorController.abort();
  actorController = new AbortController();
  actorQuery.value = actor.name;
  searchInput.value = "";
  clearTitleSearch();
  hideBrowse();
  resultsEl.replaceChildren();
  const status = document.createElement("p");
  status.className = "rail-status";
  status.textContent = `Loading titles with ${actor.name}…`;
  resultsEl.appendChild(status);
  resultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const payload = await fetchJson(`/api/actors/${actor.id}/movies`, actorController.signal);
    if (seq !== actorSeq) return;
    renderResults(actor.name, payload, "actor");
  } catch (error) {
    if (error.name === "AbortError" || seq !== actorSeq) return;
    status.textContent = "Could not load this actor’s titles. Try again.";
  }
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = searchInput.value.trim();
  if (!query) {
    clearTitleSearch();
    if (!actorQuery.value.trim()) showBrowse();
    return;
  }
  actorQuery.value = "";
  clearActorSearch();
  searchTimer = setTimeout(() => runSearch(query), 250);
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(searchTimer);
  const query = searchInput.value.trim();
  if (!query) {
    clearSearch();
    searchInput.focus();
    return;
  }
  runSearch(query);
});

actorQuery.addEventListener("input", () => {
  clearTimeout(actorTimer);
  const query = actorQuery.value.trim();
  if (!query) {
    clearActorSearch();
    if (!searchInput.value.trim()) showBrowse();
    return;
  }
  if (query.length < 2) {
    showActorHint();
    return;
  }
  searchInput.value = "";
  clearTitleSearch();
  actorTimer = setTimeout(() => runActorSearch(query), 250);
});

actorForm.addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(actorTimer);
  const query = actorQuery.value.trim();
  if (!query) {
    clearActorSearch();
    if (!searchInput.value.trim()) showBrowse();
    actorQuery.focus();
    return;
  }
  if (actorPicks.length === 1 || actorPicks.some((actor) => actor.name.toLowerCase() === query.toLowerCase())) {
    const exact = actorPicks.find((actor) => actor.name.toLowerCase() === query.toLowerCase()) || actorPicks[0];
    loadActorMovies(exact);
    return;
  }
  if (query.length < 2) {
    showActorHint();
    return;
  }
  runActorSearch(query);
});

detailEl.addEventListener("click", (event) => {
  if (event.target === detailEl) detailEl.close();
});

detailEl.addEventListener("close", clearDetailFit);

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", fitDetailDialog);
  window.visualViewport.addEventListener("scroll", fitDetailDialog);
}
window.addEventListener("resize", fitDetailDialog);

const yearFilterRoot = document.getElementById("year-filter");
const yearFilterButton = document.getElementById("year-filter-open");
const yearFilterPanel = document.getElementById("year-filter-panel");
const yearFilterSummaryEl = document.getElementById("year-filter-summary");
const decadeGrid = document.getElementById("decade-grid");
const yearRangeForm = document.getElementById("year-range-form");
const yearFromInput = document.getElementById("year-from");
const yearToInput = document.getElementById("year-to");
const yearRangeError = document.getElementById("year-range-error");
const yearRangesEl = document.getElementById("year-ranges");
const yearFilterClear = document.getElementById("year-filter-clear");

function setYearPanel(open) {
  yearFilterPanel.hidden = !open;
  yearFilterButton.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    yearFilterPanel.tabIndex = -1;
    yearFilterPanel.focus();
    return;
  }
  yearFilterButton.focus();
}

function syncYearFilterUi() {
  const summary = yearFilterSummary();
  yearFilterButton.classList.toggle("is-on", hasYearFilter());
  yearFilterSummaryEl.hidden = !summary;
  yearFilterSummaryEl.textContent = summary;
  yearFilterButton.setAttribute(
    "aria-label",
    hasYearFilter() ? `Year filter, ${yearFilterLabel()}` : "Year filter",
  );
  for (const button of decadeGrid.querySelectorAll(".decade-chip")) {
    const selected = yearFilter.decades.includes(Number(button.dataset.start));
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  }
  yearRangesEl.replaceChildren();
  for (const range of yearFilter.ranges) {
    const chip = document.createElement("span");
    chip.className = "range-chip";
    const label = document.createElement("span");
    label.textContent = rangeLabel(range);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${rangeLabel(range)}`);
    remove.addEventListener("click", () => {
      yearFilter.ranges = yearFilter.ranges.filter(
        (item) => item.from !== range.from || item.to !== range.to,
      );
      commitYearFilter();
    });
    chip.append(label, remove);
    yearRangesEl.appendChild(chip);
  }
  yearFilterClear.hidden = !hasYearFilter();
}

function refreshOpenRail() {
  if (openGenreId == null) return;
  const genre = genres.find((item) => item.id === openGenreId);
  const pending = movieCache.get(openGenreId);
  if (!genre || !pending) return;
  Promise.resolve(pending).then((movies) => {
    if (openGenreId !== genre.id || !Array.isArray(movies)) return;
    renderRail(genre, movies, false);
  }).catch(() => {});
}

function refreshResultsForYear() {
  if (resultsEl.hidden) return;
  if (resultsMode === "search") {
    const query = searchInput.value.trim();
    if (query) runSearch(query, { scroll: false });
    return;
  }
  if (resultsMode === "actor" && actorResults) {
    renderResults(actorResults.query, actorResults.payload, "actor");
  }
}

function commitYearFilter() {
  saveYearFilter();
  syncYearFilterUi();
  renderFavourites();
  renderContinueWatching();
  refreshOpenRail();
  refreshResultsForYear();
}

function showYearError(message) {
  yearRangeError.hidden = !message;
  yearRangeError.textContent = message || "";
}

for (const decade of DECADES) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "decade-chip";
  button.dataset.start = String(decade.start);
  button.textContent = decade.label;
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => {
    if (yearFilter.decades.includes(decade.start)) {
      yearFilter.decades = yearFilter.decades.filter((start) => start !== decade.start);
    } else {
      yearFilter.decades = [...yearFilter.decades, decade.start].sort((a, b) => a - b);
    }
    commitYearFilter();
  });
  decadeGrid.appendChild(button);
}

yearFilterButton.addEventListener("click", () => {
  setYearPanel(yearFilterPanel.hidden);
});

yearRangeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const fromRaw = yearFromInput.value.trim();
  const toRaw = yearToInput.value.trim();
  if (!fromRaw && !toRaw) {
    showYearError("Enter a year.");
    yearFromInput.focus();
    return;
  }
  const fromYear = parseYearField(fromRaw || toRaw);
  const toYear = parseYearField(toRaw || fromRaw);
  if (fromYear == null || toYear == null) {
    showYearError(`Use a year from ${YEAR_MIN} to ${YEAR_MAX}.`);
    return;
  }
  const range = normalizeYearRange(fromYear, toYear);
  showYearError("");
  yearFromInput.value = "";
  yearToInput.value = "";
  if (yearFilter.ranges.some((item) => item.from === range.from && item.to === range.to)) return;
  yearFilter.ranges = [...yearFilter.ranges, range].sort((a, b) => a.from - b.from || a.to - b.to);
  commitYearFilter();
});

yearFilterClear.addEventListener("click", () => {
  yearFilter = { decades: [], ranges: [] };
  showYearError("");
  yearFromInput.value = "";
  yearToInput.value = "";
  commitYearFilter();
});

document.addEventListener("click", (event) => {
  if (yearFilterPanel.hidden || yearFilterRoot.contains(event.target)) return;
  yearFilterPanel.hidden = true;
  yearFilterButton.setAttribute("aria-expanded", "false");
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (detailEl.open) {
    detailEl.close();
    return;
  }
  if (!yearFilterPanel.hidden) setYearPanel(false);
});

document.getElementById("favourites-jump").addEventListener("click", () => {
  if (searchInput.value.trim() || actorQuery.value.trim() || !resultsEl.hidden) {
    searchInput.value = "";
    actorQuery.value = "";
    clearSearch();
  }
  favouritesEl.scrollIntoView({ behavior: "smooth", block: "start" });
});

syncYearFilterUi();
renderFavourites();
refreshWatchedProgress();
if (window.MovieWorldCloud && window.MovieWorldCloud.start) window.MovieWorldCloud.start();

fetchJson("/api/genres")
  .then((rows) => {
    genres = rows;
    renderGenres();
  })
  .catch(() => {
    const message = document.createElement("p");
    message.className = "rail-status";
    message.textContent = "Could not load genres.";
    genresEl.replaceWith(message);
  });

const headerMenu = document.getElementById("header-menu");
const headerMenuToggle = document.getElementById("header-menu-toggle");
function setHeaderMenu(open, restoreFocus = false) {
  headerMenu.classList.toggle("is-open", open);
  headerMenuToggle.setAttribute("aria-expanded", String(open));
  if (!open) {
    setYearPanel(false);
    const accountPanel = document.getElementById("account-panel");
    if (accountPanel) accountPanel.hidden = true;
    if (restoreFocus) headerMenuToggle.focus();
  }
}
headerMenuToggle.addEventListener("click", () => {
  setHeaderMenu(!headerMenu.classList.contains("is-open"));
});
document.addEventListener("click", (event) => {
  if (!headerMenu.contains(event.target)) setHeaderMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && headerMenu.classList.contains("is-open")) {
    setHeaderMenu(false, true);
  }
});
headerMenu.addEventListener("focusout", (event) => {
  if (event.relatedTarget && !headerMenu.contains(event.relatedTarget)) setHeaderMenu(false);
});
for (const id of ["favourites-jump", "history-open"]) {
  document.getElementById(id).addEventListener("click", () => setHeaderMenu(false));
}
