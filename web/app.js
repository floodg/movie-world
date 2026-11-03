const FAVOURITES_KEY = "movie-world-favourites";
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

function renderRail(genre, movies) {
  railEl.replaceChildren();
  const bar = document.createElement("div");
  bar.className = "rail-bar";
  const heading = document.createElement("h2");
  heading.textContent = genre.name;
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
  bar.append(heading, nav);

  const scroller = document.createElement("div");
  scroller.className = "scroller";
  for (const movie of movies) scroller.appendChild(movieCard(movie));

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
  railEl.append(bar, scroller);
  requestAnimationFrame(syncNav);
  scrollToMovieList();
}

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

function loadFavourites() {
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

function normalizeFavourite(item) {
  if (!item || typeof item !== "object") return null;
  const id = Number(item.id);
  if (!Number.isInteger(id) || id <= 0) return null;
  if (typeof item.title !== "string" || !item.title) return null;
  return {
    id,
    title: item.title,
    year: Number.isInteger(item.year) ? item.year : null,
    rating: typeof item.rating === "number" ? item.rating : null,
    poster_url: typeof item.poster_url === "string" ? item.poster_url : null,
    media_type: typeof item.media_type === "string" ? item.media_type : null,
  };
}

function saveFavourites() {
  localStorage.setItem(FAVOURITES_KEY, JSON.stringify(favourites));
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
  };
}

function toggleFavourite(movie) {
  if (isFavourite(movie.id)) {
    favourites = favourites.filter((item) => item.id !== movie.id);
  } else {
    favourites = [favouriteSnapshot(movie), ...favourites];
  }
  saveFavourites();
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
  const bar = document.createElement("div");
  bar.className = "rail-bar";
  const heading = document.createElement("h2");
  heading.id = "favourites-heading";
  heading.textContent = "Favourites";
  bar.appendChild(heading);
  favouritesEl.appendChild(bar);

  if (favourites.length === 0) {
    const status = document.createElement("p");
    status.className = "rail-status";
    status.textContent = "Save a title with the heart and it stays in this list.";
    favouritesEl.appendChild(status);
    return;
  }

  const grid = document.createElement("div");
  grid.className = "favourites-grid";
  for (const movie of favourites) grid.appendChild(movieCard(movie));
  favouritesEl.appendChild(grid);
}

async function openDetail(movieId) {
  detailEl.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "detail-body";
  const message = document.createElement("p");
  message.textContent = "Loading…";
  loading.appendChild(message);
  detailEl.appendChild(loading);
  if (!detailEl.open) detailEl.showModal();
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
  }
  body.appendChild(actions);
  detailEl.append(hero, body);
}

function textSpan(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span;
}

function showBrowse() {
  resultsEl.hidden = true;
  resultsEl.replaceChildren();
  favouritesEl.hidden = false;
  genresEl.hidden = false;
  railEl.hidden = openGenreId == null;
}

function hideBrowse() {
  favouritesEl.hidden = true;
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

function showSearchPending(query) {
  hideBrowse();
  resultsEl.replaceChildren();
  const status = document.createElement("p");
  status.className = "rail-status";
  status.textContent = `Searching for “${query}”…`;
  resultsEl.appendChild(status);
  resultsEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderResults(query, payload, kind) {
  resultsEl.replaceChildren();
  const movies = payload.movies;
  const bar = document.createElement("div");
  bar.className = "results-bar";
  const heading = document.createElement("h2");
  const note = document.createElement("p");
  note.className = "results-query";
  if (!movies.length) {
    heading.textContent = "No titles match";
    note.textContent = `Nothing matches “${query}”.`;
    bar.append(heading, note);
    resultsEl.appendChild(bar);
    return;
  }
  const shown = movies.length === payload.total
    ? `${payload.total.toLocaleString()} ${payload.total === 1 ? "title" : "titles"}`
    : `${movies.length} of ${payload.total.toLocaleString()}`;
  heading.textContent = shown;
  note.textContent = kind === "actor" ? `With ${query}` : `Matching “${query}”`;
  bar.append(heading, note);
  const grid = document.createElement("div");
  grid.className = "results-grid";
  for (const movie of movies) grid.appendChild(movieCard(movie));
  resultsEl.append(bar, grid);
}

async function runSearch(query) {
  const seq = ++searchSeq;
  if (searchController) searchController.abort();
  searchController = new AbortController();
  showSearchPending(query);
  try {
    const payload = await fetchJson(
      `/api/search?q=${encodeURIComponent(query)}`,
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

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && detailEl.open) detailEl.close();
});

document.getElementById("favourites-jump").addEventListener("click", () => {
  if (searchInput.value.trim() || actorQuery.value.trim() || !resultsEl.hidden) {
    searchInput.value = "";
    actorQuery.value = "";
    clearSearch();
  }
  favouritesEl.scrollIntoView({ behavior: "smooth", block: "start" });
});

renderFavourites();

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
