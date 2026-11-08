/* UI port of the Kotlin Compose screens. Player/PC transport lives in tizen.js. */
(() => {
  'use strict';
  const screen = document.getElementById('screen');
  const detail = document.getElementById('detail');
  const playIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" stroke="none" d="M8 4v16l13-8z"/></svg>';
  const listIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
  const checkIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';
  let route = 'home', home = null, detailMovie = null, previousFocus = null, detailVersion = 0;
  let favourites = read('movie-world-favourites').filter(m => !m.removed_at);
  let history = read('movie-world-watch-progress');
  let resume = [];
  let query = '', mode = 'title', genre = '', from = '', to = '', searchVersion = 0, debounce;
  let selectedActor = null, actorPicks = [];

  function read(key) {
    try { const items = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(items) ? items : []; } catch (e) { return []; }
  }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* Keep this visit usable. */ } }
  function el(tag, className, text) {
    const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node;
  }
  function button(label, action, className = '', icon = '') {
    const node = el('button', className); node.type = 'button';
    if (icon) node.innerHTML = icon;
    node.appendChild(el('span', '', label)); node.onclick = action; return node;
  }
  function empty(parent, title, body) {
    const node = el('div', 'empty'); node.append(el('h2', '', title), el('p', '', body)); parent.appendChild(node);
  }
  function inList(movie) { return favourites.some(m => String(m.id) === String(movie.id)); }
  function toggle(movie) {
    favourites = inList(movie) ? favourites.filter(m => String(m.id) !== String(movie.id)) : [movie, ...favourites];
    save('movie-world-favourites', favourites);
    document.querySelectorAll('[data-list-id]').forEach(node => {
      if (node.dataset.listId === String(movie.id)) {
        node.querySelector('svg').outerHTML = inList(movie) ? checkIcon : listIcon;
        node.lastChild.textContent = inList(movie) && node.dataset.hero !== 'true' ? 'In My List' : 'My List';
      }
    });
  }
  function listButton(movie, hero = false) {
    const node = button(inList(movie) && !hero ? 'In My List' : 'My List', () => toggle(movie), 'secondary', inList(movie) ? checkIcon : listIcon);
    node.dataset.listId = String(movie.id); node.dataset.hero = String(hero); return node;
  }
  function point(id) { return resume.find(p => String(p.movie_id) === String(id)); }
  function time(seconds) {
    const value = Math.max(0, Math.floor(seconds || 0)); return Math.floor(value / 60) + ':' + ('0' + value % 60).slice(-2);
  }
  function playButton(movie, restart = false) {
    const saved = point(movie.id);
    return button(restart ? 'Start over' : saved ? 'Resume from ' + time(saved.position) : 'Play', () => window.MovieWorldTv.play(movie, restart), restart ? 'secondary' : 'primary', restart ? '' : playIcon);
  }
  function safeImage(url) {
    try { const parsed = new URL(url); return parsed.protocol === 'https:' && parsed.hostname === 'image.tmdb.org'; } catch (e) { return false; }
  }
  function image(url, className = '') {
    if (!safeImage(url)) return null;
    const node = el('img', className); node.alt = ''; node.src = url; node.onerror = () => node.hidden = true; return node;
  }
  function card(movie, showRating = false) {
    const node = button('', () => openDetail(movie.id), 'poster'); node.textContent = '';
    node.dataset.movieId = String(movie.id);
    node.setAttribute('aria-label', movie.title + (movie.year ? ', ' + movie.year : ''));
    node.appendChild(el('span', 'fallback', movie.title));
    const poster = image(movie.poster_url); if (poster) { poster.loading = 'lazy'; node.appendChild(poster); }
    if (showRating && movie.rating != null) {
      const rating = el('span', 'rating'); rating.append(el('b', '', '★'), document.createTextNode(String(movie.rating))); node.appendChild(rating);
    }
    const saved = point(movie.id);
    if (saved && saved.duration) {
      const progress = el('span', 'progress'), fill = el('span'); fill.style.width = Math.min(100, saved.position / saved.duration * 100) + '%'; progress.appendChild(fill); node.appendChild(progress);
    }
    return node;
  }
  function rail(parent, title, movies) {
    if (!movies || !movies.length) return;
    const section = el('section', 'rail'); section.setAttribute('aria-label', title); section.appendChild(el('h2', '', title));
    const row = el('div', 'poster-row'); movies.slice(0, 24).forEach(movie => row.appendChild(card(movie))); section.appendChild(row); parent.appendChild(section);
  }
  function renderHome() {
    screen.textContent = '';
    if (!home) { screen.appendChild(el('div', 'hero skeleton')); return; }
    const movie = home.featured;
    if (movie) {
      const hero = el('section', 'hero'), backdrop = el('div', 'hero-image');
      if (safeImage(movie.backdrop_url || movie.poster_url)) backdrop.style.backgroundImage = 'url("' + (movie.backdrop_url || movie.poster_url) + '")';
      hero.append(backdrop, el('div', 'hero-shade'));
      const copy = el('div', 'hero-copy'); copy.appendChild(el('h1', '', movie.title));
      copy.appendChild(el('p', 'meta', [movie.year, movie.rating ? 'IMDb ' + movie.rating : '', movie.genres].filter(Boolean).join('  ·  ')));
      const actions = el('div', 'actions'); actions.append(playButton(movie), listButton(movie, true));
      copy.append(actions, button('Details', () => openDetail(movie.id), 'text-button')); hero.appendChild(copy); screen.appendChild(hero);
    }
    rail(screen, 'Continue Watching', home.continuing);
    rail(screen, 'Recently Watched', history.filter(p => p.movie).map(p => p.movie));
    rail(screen, 'My List', favourites);
    rail(screen, 'Top Rated', home.top_rated);
    home.rows.forEach(row => rail(screen, row.genre.name, row.movies));
  }
  function renderList() {
    screen.textContent = ''; const page = el('section', 'page'); page.appendChild(el('h1', '', 'My List'));
    if (!favourites.length) empty(page, 'Nothing saved yet', 'Select My List on any movie to keep it here for later.');
    else { const grid = el('div', 'grid'); favourites.forEach(movie => grid.appendChild(card(movie, true))); page.appendChild(grid); }
    screen.appendChild(page);
  }
  function showTab(next, focus = true) {
    route = next; searchVersion++; window.scrollTo(0, 0); screen.setAttribute('aria-label', next === 'list' ? 'My List' : next === 'search' ? 'Search' : 'Home');
    document.querySelectorAll('[data-tab]').forEach(node => { if (node.dataset.tab === next) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current'); });
    if (next === 'home') renderHome(); else if (next === 'list') renderList(); else renderSearch();
    if (focus) { const first = screen.querySelector('input,button'); if (first) first.focus(); }
  }
  function renderSearch() {
    screen.textContent = ''; const page = el('section', 'page'); page.appendChild(el('h1', '', 'Search'));
    const form = el('form', 'search-box'), input = el('input'); input.placeholder = 'Movies, actors…'; input.value = query; input.maxLength = 80; input.setAttribute('aria-label', 'Movies, actors');
    input.id = 'actor-query'; input.placeholder = mode === 'actor' ? 'Search by actor' : 'Search by title';
    if (mode === 'actor') { input.setAttribute('role','combobox'); input.setAttribute('aria-autocomplete','list'); input.setAttribute('aria-controls','actor-picks'); input.setAttribute('aria-expanded','false'); }
    input.oninput = () => {
      query = input.value; selectedActor = null; actorPicks = []; searchVersion++;
      hideActorPicks(); clearTimeout(debounce); debounce = setTimeout(runSearch, 250);
    };
    const submit = () => {
      clearTimeout(debounce); query = input.value;
      if (mode === 'actor' && !selectedActor) {
        const exact = actorPicks.find(actor => actor.name.toLowerCase() === query.trim().toLowerCase());
        if (exact || actorPicks.length === 1) { pickActor(exact || actorPicks[0]); return; }
      }
      runSearch();
    };
    form.onsubmit = event => { event.preventDefault(); submit(); };
    form.append(input, button('Search', submit));
    const filtersButton = button('Filters', () => { hideActorPicks(); filters.hidden = !filters.hidden; if (!filters.hidden) filters.querySelector('input').focus(); }); form.appendChild(filtersButton);
    const searchField = el('div','search-field'); searchField.appendChild(form);
    const picks = el('div','actor-dropdown'); picks.id = 'actor-picks'; picks.setAttribute('role','listbox'); picks.setAttribute('aria-label','Matching actors'); picks.hidden = true; searchField.appendChild(picks); page.appendChild(searchField);
    const segments = el('div', 'segments'); ['title', 'actor'].forEach(value => {
      segments.appendChild(button(value === 'title' ? 'Title' : 'Actor', () => { mode = value; selectedActor = null; actorPicks = []; renderSearch(); screen.querySelector('.segments .selected').focus(); }, value === mode ? 'selected' : ''));
    }); page.appendChild(segments);
    const chips = el('div', 'chips');
    [{id:'',name:'All genres'}, ...((home || {}).genres || [])].forEach(item => {
      chips.appendChild(button(item.name, () => { genre = String(item.id); renderSearch(); const chosen = screen.querySelector('.chips .selected'); if (chosen) chosen.focus(); }, String(item.id) === genre ? 'selected' : ''));
    }); page.appendChild(chips);
    const filters = el('form', 'filters'); filters.hidden = true;
    const first = el('input'), last = el('input'); first.type = last.type = 'number'; first.min = last.min = 1880; first.max = last.max = 2100;
    first.placeholder = 'From'; first.value = from; first.setAttribute('aria-label', 'From year'); last.placeholder = 'To'; last.value = to; last.setAttribute('aria-label', 'To year');
    filters.append(el('span', '', 'Year'), first, last);
    const filterError = el('span', 'muted'); filterError.setAttribute('role', 'alert');
    const apply = () => {
      if ((first.value && !first.checkValidity()) || (last.value && !last.checkValidity()) || (first.value && last.value && Number(first.value) > Number(last.value))) { filterError.textContent = 'Enter a valid year range (1880–2100).'; return; }
      from = first.value; to = last.value; filters.hidden = true; filtersButton.focus(); runSearch();
    };
    filters.append(button('Apply', apply, 'primary'), button('Clear', () => { from = to = ''; first.value = last.value = ''; apply(); }), filterError);
    filters.onsubmit = event => { event.preventDefault(); apply(); }; page.appendChild(filters);
    const results = el('div'); results.id = 'search-results'; results.setAttribute('aria-live', 'polite'); page.appendChild(results); screen.appendChild(page); runSearch();
  }
  function hideActorPicks() {
    const picks = document.getElementById('actor-picks'); if (picks) picks.hidden = true;
    const input = document.getElementById('actor-query'); if (input) input.setAttribute('aria-expanded','false');
  }
  function pickActor(actor) {
    selectedActor = actor; actorPicks = []; query = actor.name;
    const input = document.getElementById('actor-query'); input.value = query; hideActorPicks(); input.focus(); runSearch();
  }
  async function runSearch() {
    const target = document.getElementById('search-results'); if (!target) return;
    const version = ++searchVersion; target.textContent = 'Searching…';
    const years = from || to ? (from || '1880') + '-' + (to || '2100') : '';
    try {
      if (mode === 'actor' && !selectedActor) {
        target.textContent = ''; hideActorPicks();
        if (query.trim().length < 2) { target.textContent = 'Type at least two letters of an actor’s name.'; return; }
        target.textContent = 'Searching actors…';
        const result = await window.tvRequest('/api/actors?q=' + encodeURIComponent(query));
        if (version !== searchVersion || route !== 'search') return;
        actorPicks = result.actors; target.textContent = actorPicks.length ? 'Choose an actor to view their movies.' : 'No actors match';
        const picks = document.getElementById('actor-picks'); picks.textContent = '';
        actorPicks.forEach(actor => {
          const option = button('', () => pickActor(actor), 'actor-option'); option.textContent = '';
          option.setAttribute('role','option'); option.setAttribute('aria-selected','false');
          option.append(el('strong','',actor.name), el('span','',actor.movie_count.toLocaleString() + (actor.movie_count === 1 ? ' movie' : ' movies'))); picks.appendChild(option);
        });
        picks.hidden = !actorPicks.length;
        document.getElementById('actor-query').setAttribute('aria-expanded', String(actorPicks.length > 0));
        return;
      }
      hideActorPicks();
      const result = await window.tvRequest('/api/tizen-catalogue/search?q=' + encodeURIComponent(query) + '&mode=' + mode + '&actor_id=' + encodeURIComponent(selectedActor ? selectedActor.id : '') + '&genre=' + encodeURIComponent(genre) + '&years=' + years);
      if (version !== searchVersion || route !== 'search') return;
      target.textContent = '';
      if (selectedActor) target.appendChild(el('p','muted', 'Movies with ' + selectedActor.name));
      if (!result.movies.length) empty(target, 'No matching movies', 'Try another spelling, switch between Title and Actor, or widen the filters.');
      else { const grid = el('div', 'grid'); grid.setAttribute('aria-label', 'Search results'); result.movies.forEach(movie => grid.appendChild(card(movie, true))); target.appendChild(grid); }
    } catch (e) { if (version === searchVersion) { target.textContent = ''; empty(target, 'Could not load movies', e.message); target.appendChild(button('Retry', runSearch)); } }
  }
  async function openDetail(id) {
    if (detail.hidden) previousFocus = document.activeElement;
    const version = ++detailVersion;
    detail.hidden = false; document.body.classList.add('detail-open'); detail.textContent = ''; detail.scrollTop = 0;
    const back = button('←', closeDetail, 'back'); back.setAttribute('aria-label', 'Back'); detail.appendChild(back); back.focus();
    const loading = el('div', 'page', 'Loading movie…'); detail.appendChild(loading);
    try {
      const movie = await window.tvRequest('/api/movies/' + id);
      if (version !== detailVersion) return;
      detailMovie = movie; loading.remove();
      const cover = el('div', 'detail-cover'), backdrop = image(movie.backdrop_url || movie.poster_url); if (backdrop) cover.appendChild(backdrop); detail.appendChild(cover);
      const content = el('div', 'detail-content'); content.appendChild(el('h1', '', movie.title));
      const meta = el('p', 'meta'); meta.appendChild(document.createTextNode(String(movie.year || '')));
      if (movie.rating != null) { const rating = el('span', 'rating'); rating.append(el('b', '', '★'), document.createTextNode(String(movie.rating))); meta.appendChild(rating); }
      meta.appendChild(document.createTextNode(movie.genres || '')); content.appendChild(meta);
      const actions = el('div', 'actions'); actions.append(playButton(movie), listButton(movie)); if (point(movie.id)) actions.appendChild(playButton(movie, true)); content.appendChild(actions);
      if (movie.overview) content.appendChild(el('p', 'overview', movie.overview));
      if (movie.cast && movie.cast.length) {
        content.appendChild(el('h2', '', 'Cast')); const cast = el('div', 'cast'); movie.cast.forEach(actor => cast.appendChild(button(actor.name, () => { closeDetail(); mode = 'actor'; selectedActor = actor; query = actor.name; showTab('search'); }))); content.appendChild(cast);
      }
      detail.appendChild(content);
      const related = await window.tvRequest('/api/movies/' + id + '/similar'); if (version === detailVersion) rail(content, 'More like this', related);
    } catch (e) { if (version === detailVersion) { loading.textContent = ''; empty(loading, 'Movie details unavailable', e.message); loading.appendChild(button('Retry', () => openDetail(id))); } }
  }
  function closeDetail() {
    const id = detailMovie && detailMovie.id;
    detailVersion++; detail.hidden = true; detailMovie = null; document.body.classList.remove('detail-open');
    if (previousFocus && document.contains(previousFocus)) previousFocus.focus();
    else { const restored = id && screen.querySelector('[data-movie-id="' + id + '"]'); if (restored) restored.focus(); }
  }
  async function loadHome() {
    const today = new Date(), day = Math.floor((Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - Date.UTC(today.getFullYear(), 0, 0)) / 86400000);
    try {
      const result = await window.tvRequest('/api/tizen-catalogue/home?day=' + day);
      home = result;
      try { const progress = await window.tvRequest('/api/resume'); resume = progress.points; home.continuing = progress.movies; } catch (e) { home.continuing = []; }
      if (route === 'home') renderHome();
    } catch (e) {
      if (route === 'home') { screen.textContent = ''; const page = el('div', 'page'); empty(page, 'Could not load the catalogue', e.message); page.appendChild(button('Retry', loadHome)); screen.appendChild(page); }
    }
  }
  document.querySelectorAll('[data-tab]').forEach(node => node.onclick = () => showTab(node.dataset.tab));
  document.querySelector('.wordmark').onclick = event => { event.preventDefault(); showTab('home'); };
  window.addEventListener('scroll', () => document.querySelector('.top').classList.toggle('solid', window.scrollY > 60));
  window.addEventListener('mw-progress', event => {
    const data = event.detail;
    let movie = detailMovie && String(detailMovie.id) === String(data.movie_id) ? detailMovie : home && home.featured && String(home.featured.id) === String(data.movie_id) ? home.featured : null;
    if (!movie && home) { for (const row of home.rows) { movie = row.movies.find(m => String(m.id) === String(data.movie_id)); if (movie) break; } }
    history = [{...data, movie, updated_at:Date.now() / 1000}, ...history.filter(p => String(p.movie_id) !== String(data.movie_id))].slice(0, 24);
    save('movie-world-watch-progress', history);
  });
  window.addEventListener('mw-playback-closed', () => { loadHome(); });
  document.addEventListener('keydown', event => {
    const key = event.keyCode;
    if (document.body.classList.contains('mw-playing')) return;
    const settings = document.getElementById('connection');
    const picks = document.getElementById('actor-picks'), actorInput = document.getElementById('actor-query');
    if (picks && !picks.hidden && detail.hidden && settings.hidden) {
      if (key === 27 || key === 10009) { event.preventDefault(); hideActorPicks(); actorInput.focus(); return; }
      if (key === 40 && document.activeElement === actorInput) { event.preventDefault(); picks.querySelector('button').focus(); return; }
      if (key === 38 && document.activeElement === picks.firstChild) { event.preventDefault(); actorInput.focus(); return; }
      if ((key === 38 || key === 40) && picks.contains(document.activeElement)) {
        event.preventDefault();
        const options = Array.from(picks.children), index = options.indexOf(document.activeElement);
        options[Math.max(0, Math.min(options.length - 1, index + (key === 40 ? 1 : -1)))].focus();
        return;
      }
    }
    if (key === 10009 || key === 27) {
      event.preventDefault();
      if (!settings.hidden) document.getElementById('settings-close').click();
      else if (!detail.hidden) closeDetail();
      else if (route !== 'home') showTab('home');
      else if (window.tizen) window.tizen.application.getCurrentApplication().exit();
      return;
    }
    const root = !settings.hidden ? settings : !detail.hidden ? detail : document.body;
    const items = Array.from(root.querySelectorAll('button,a,input,select')).filter(node => !node.disabled && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
    const current = document.activeElement;
    if (key === 9 && root !== document.body) {
      event.preventDefault(); const index = items.indexOf(current); if (items.length) items[(index + (event.shiftKey ? -1 : 1) + items.length) % items.length].focus(); return;
    }
    if (key < 37 || key > 40 || (current.tagName === 'INPUT' && (key === 37 || key === 39))) return;
    event.preventDefault();
    const box = current.getBoundingClientRect(), x = box.left + box.width / 2, y = box.top + box.height / 2;
    let best = null, score = Infinity;
    items.forEach(node => {
      if (node === current) return;
      const rect = node.getBoundingClientRect(), dx = rect.left + rect.width / 2 - x, dy = rect.top + rect.height / 2 - y;
      const forward = key === 37 ? -dx : key === 39 ? dx : key === 38 ? -dy : dy;
      const side = key === 37 || key === 39 ? Math.abs(dy) : Math.abs(dx);
      if (forward <= 1) return;
      const distance = forward + side * 3;
      if (distance < score) { best = node; score = distance; }
    });
    if (!best && !items.includes(current)) best = items[0];
    if (best) { best.focus(); best.scrollIntoView({block:'nearest',inline:'nearest'}); }
  });
  showTab('home', false); loadHome();
})();
