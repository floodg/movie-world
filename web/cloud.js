/** PC-local personal storage, synchronized by the server through its Telegram account. */
(function () {
  const QUEUE = "movie-world-pc-sync-queue";
  let scope = localStorage.getItem("movie-world-pc-account");
  let inFlight = null;
  let ready = false;
  let queue = [];
  let timer;
  try { queue = JSON.parse(localStorage.getItem(QUEUE) || "[]"); if (!Array.isArray(queue)) queue = []; } catch (_) {}
  function persist() { try { localStorage.setItem(QUEUE, JSON.stringify(queue)); } catch (_) {} }
  function status(message) { const el = document.getElementById("account-status"); if (el) el.textContent = message; }
  async function api(path, body) {
    const response = await fetch(path, { method: body ? "POST" : "GET", credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "PC storage unavailable");
    return data;
  }
  function ms(value) {
    if (typeof value === "number") return Math.max(0, Math.floor(value < 1e12 ? value * 1000 : value));
    return Math.max(0, Date.parse(value) || 0);
  }
  function favourite(row) { return { movieId: String(row.movie_id), updated: ms(row.updated_at),
    removed: row.removed_at ? ms(row.removed_at) : null }; }
  function progress(row) { return { movieId: String(row.movie_id), updated: ms(row.updated_at),
    position: Math.max(0, Math.floor(Number(row.position_seconds) || 0)),
    duration: Math.max(0, Math.floor(Number(row.duration_seconds) || 0)), finished: Boolean(row.finished) }; }
  async function apply(data) {
    const next = data.scope;
    await window.MovieWorldSync.setAccountScope(next);
    scope = next;
    const snapshot = data.snapshot;
    await window.MovieWorldSync.mergeFavouritesFromCloud(snapshot.favourites.map((row) => ({
      movie_id: row.movieId, updated_at: new Date(row.updated).toISOString(),
      removed_at: row.removed == null ? null : new Date(row.removed).toISOString(),
    })));
    await window.MovieWorldSync.mergeWatchFromCloud(snapshot.progress.map((row) => ({
      movie_id: row.movieId, position_seconds: row.position, duration_seconds: row.duration,
      finished: row.finished, updated_at: new Date(row.updated).toISOString(),
    })));
    status(data.status.message + (data.status.pending ? " · changes pending" : ""));
    const last = document.getElementById("account-last-sync");
    if (last) last.textContent = data.status.lastSync ? "Last sync: " + new Date(data.status.lastSync).toLocaleString() : "";
  }
  function enqueue(kind, row) {
    queue = queue.filter((item) => !(item.scope === scope && item.kind === kind && item.row.movieId === row.movieId));
    queue.push({ scope, kind, row }); persist();
    clearTimeout(timer); timer = setTimeout(refresh, 400);
  }
  async function flush() {
    while (queue.length) {
      const eligible = queue.filter((item) => !item.scope || item.scope === scope || item.scope === "local");
      if (!eligible.length) break;
      const batch = eligible.slice(0, 20);
      const body = { scope, favourites: [], progress: [] };
      batch.forEach((item) => body[item.kind].push(item.row));
      await api("/api/personal/merge", body);
      queue = queue.filter((item) => !batch.includes(item)); persist();
    }
  }
  async function refresh(force) {
    if (inFlight) return inFlight;
    inFlight = (async function () {
      try {
        const data = await api("/api/personal");
        // Import pre-existing browser favourites/history once per PC account before replacing the UI cache.
        await window.MovieWorldSync.setAccountScope(data.scope);
        scope = data.scope;
        const marker = "movie-world-pc-migrated-" + scope;
        if (!localStorage.getItem(marker)) {
          const oldFav = window.MovieWorldSync.exportFavouritesForCloud().map(favourite);
          const oldWatch = window.MovieWorldSync.exportWatchForCloud().map(progress);
          oldFav.forEach((row) => enqueue("favourites", row));
          oldWatch.forEach((row) => enqueue("progress", row));
          await flush();
          localStorage.setItem(marker, "yes");
        } else await flush();
        ready = true;
        await apply(force === true ? await api("/api/personal/sync", {}) : await api("/api/personal"));
      } catch (_) { status("PC sync unavailable · browser copy kept; retrying automatically"); }
    })();
    try { await inFlight; } finally { inFlight = null; }
  }
  window.MovieWorldCloud = {
    enabled: true,
    session() { return ready ? { user: { id: scope } } : null; },
    async start() {
      const root = document.getElementById("account"); if (root) root.hidden = false;
      document.getElementById("account-toggle")?.addEventListener("click", function () {
        const panel = document.getElementById("account-panel"); panel.hidden = !panel.hidden;
      });
      document.getElementById("account-sync-now")?.addEventListener("click", () => refresh(true));
      window.addEventListener("online", () => refresh());
      document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
      setInterval(refresh, 15000);
      await refresh();
    },
    async syncFavourites() { await refresh(); },
    async syncWatchProgress() { await refresh(); },
    async pushFavourite(movieId, removedAt, updatedAt) { enqueue("favourites", favourite({ movie_id: movieId, removed_at: removedAt, updated_at: updatedAt })); },
    async pushWatchProgress(row) { enqueue("progress", progress(row)); },
  };
})();
