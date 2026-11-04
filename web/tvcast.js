(() => {
  const RENDERER_KEY = "movie-world-tv-renderer";
  const POLL_MS = 1500;
  const STATE_LABELS = {
    queued: "Queued",
    downloading: "Downloading",
    checking: "Checking format",
    preparing: "Preparing",
    starting: "Starting TV",
    playing: "Playing",
    paused: "Paused",
    stopped: "Stopped",
    ended: "Finished",
    cancelled: "Cancelled",
    failed: "Failed",
  };
  const ON_TV = new Set(["playing", "paused"]);

  let session = null;
  let sessionPromise = null;
  let renderers = [];
  let job = null;
  let pollTimer = null;
  let panel = null;
  let panelMovie = null;
  let resumePoint = null;
  let notice = "";
  const bar = document.createElement("section");
  bar.className = "tv-bar";
  bar.hidden = true;
  bar.setAttribute("aria-live", "polite");
  bar.setAttribute("aria-label", "TV playback");
  document.body.appendChild(bar);

  function requestKey() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function api(method, path, body) {
    const headers = {};
    if (method !== "GET") headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      const error = new Error(data.error || `Request failed (${response.status})`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function loadSession() {
    if (!sessionPromise) {
      sessionPromise = api("GET", "/api/tv/session")
        .then((data) => {
          session = data;
          return data;
        })
        .catch(() => {
          session = { enabled: false };
          return session;
        });
    }
    return sessionPromise;
  }

  function formatBytes(bytes) {
    if (bytes == null) return "";
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
    return `${Math.round(bytes / 1e6).toLocaleString()} MB`;
  }

  function formatDuration(seconds) {
    if (seconds == null) return "";
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
  }

  function formatEta(seconds) {
    if (seconds == null) return "";
    if (seconds < 90) return `${seconds}s left`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min left`;
    return `${(seconds / 3600).toFixed(1)} h left`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function button(label, onClick, className = "tv-btn") {
    const node = el("button", className, label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function progressLine(current) {
    const progress = current.progress;
    if (!progress) return null;
    const wrap = el("div", "tv-progress");
    const meter = el("progress");
    let text = "";
    if (progress.fraction != null) {
      meter.max = 1;
      meter.value = progress.fraction;
      text = `${Math.round(progress.fraction * 100)}%`;
    } else if (progress.total) {
      meter.max = progress.total;
      meter.value = progress.done;
      const parts = [`${formatBytes(progress.done)} of ${formatBytes(progress.total)}`];
      if (progress.speed) parts.push(`${(progress.speed / 1e6).toFixed(1)} MB/s`);
      if (progress.eta != null) parts.push(formatEta(progress.eta));
      text = parts.join(" · ");
    } else {
      text = formatBytes(progress.done);
    }
    wrap.append(meter, el("span", "tv-progress-text", text));
    return wrap;
  }

  function jobView(current, compact) {
    const box = el("div", "tv-job");
    const head = el("p", "tv-job-head");
    const state = el("strong", `tv-state is-${current.state}`, STATE_LABELS[current.state] || current.state);
    head.append(state, ` ${current.title}`);
    if (current.renderer && current.renderer.name && !compact) head.append(el("span", "tv-muted", ` on ${current.renderer.name}`));
    box.appendChild(head);

    if (current.error) {
      box.appendChild(el("p", "tv-error", current.error.message));
    } else if (current.detail && current.detail !== STATE_LABELS[current.state]) {
      box.appendChild(el("p", "tv-muted", current.detail));
    }
    const progress = progressLine(current);
    if (progress) box.appendChild(progress);
    if (ON_TV.has(current.state) && current.duration) {
      box.appendChild(el("p", "tv-muted", `${formatDuration(current.position || 0)} / ${formatDuration(current.duration)}`));
    }

    const controls = el("div", "tv-controls");
    if (current.active) {
      if (ON_TV.has(current.state) && current.can_seek) {
        controls.appendChild(button("−30s", () => control("seek", Math.max(0, (current.position || 0) - 30))));
      }
      if (current.state === "playing" && current.can_pause) controls.appendChild(button("Pause", () => control("pause")));
      if (current.state === "paused") controls.appendChild(button("Resume", () => control("resume")));
      if (ON_TV.has(current.state) && current.can_seek) {
        controls.appendChild(button("+30s", () => control("seek", (current.position || 0) + 30)));
      }
      const stopLabel = ON_TV.has(current.state) || current.state === "starting" ? "Stop" : "Cancel";
      controls.appendChild(button(stopLabel, cancelJob, "tv-btn is-danger"));
    } else {
      if (current.error && current.error.retryable) {
        controls.appendChild(button("Try again", () => startPlayback(current.movie_id, current.renderer.id, false)));
      }
      controls.appendChild(button("Dismiss", dismiss));
    }
    box.appendChild(controls);
    return box;
  }

  function renderBar() {
    bar.replaceChildren();
    if (!job) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    bar.appendChild(jobView(job, true));
  }

  function renderPanel() {
    if (!panel || !panel.isConnected) {
      panel = null;
      return;
    }
    panel.replaceChildren();
    if (!session || !session.enabled) return;
    const row = el("div", "tv-row");
    const label = el("label", "tv-label", "TV");
    const select = el("select", "tv-select");
    select.id = "tv-renderer";
    label.htmlFor = select.id;
    const saved = localStorage.getItem(RENDERER_KEY);
    if (!renderers.length) {
      const option = el("option", null, "No TVs found");
      option.value = "";
      select.appendChild(option);
    }
    for (const renderer of renderers) {
      const option = el("option", null, renderer.available ? renderer.name : `${renderer.name} (not found)`);
      option.value = renderer.id;
      if (renderer.id === saved) option.selected = true;
      select.appendChild(option);
    }
    select.addEventListener("change", () => localStorage.setItem(RENDERER_KEY, select.value));
    const refresh = button("Search again", () => loadRenderers(true));
    const playFrom = (startAt) => {
      if (!select.value) {
        notice = "Turn the TV on, then search again.";
        renderPanel();
        return;
      }
      localStorage.setItem(RENDERER_KEY, select.value);
      startPlayback(panelMovie.id, select.value, false, startAt);
    };
    const plays = resumePoint
      ? [
          button(`Resume from ${formatDuration(resumePoint.position)}`, () => playFrom(resumePoint.position), "tv-btn is-primary"),
          button("Start over", () => playFrom(0)),
        ]
      : [button("Play on TV", () => playFrom(undefined), "tv-btn is-primary")];
    for (const play of plays) play.disabled = !panelMovie.telegram_url;
    row.append(label, select, refresh, ...plays);
    panel.appendChild(row);
    if (notice) panel.appendChild(el("p", "tv-error", notice));
    if (job) panel.appendChild(jobView(job, false));
  }

  function render() {
    renderBar();
    renderPanel();
  }

  async function loadRenderers(refresh) {
    if (refresh) {
      notice = "Searching for TVs…";
      renderPanel();
    }
    try {
      const data = await api("GET", `/api/tvs${refresh ? "?refresh=1" : ""}`);
      renderers = data.renderers;
      notice = "";
    } catch (error) {
      notice = error.message;
    }
    renderPanel();
  }

  async function loadResume(movieId) {
    try {
      const data = await api("GET", `/api/resume/${movieId}`);
      if (panelMovie && panelMovie.id === movieId) {
        resumePoint = data.resume;
        renderPanel();
      }
    } catch {
      // Resume point is optional; playback can still start from the beginning.
    }
  }

  async function startPlayback(movieId, rendererId, replace, startAt) {
    notice = "";
    try {
      const data = await api("POST", "/api/playback", {
        movie_id: movieId,
        renderer_id: rendererId,
        idempotency_key: requestKey(),
        replace,
        start_at: startAt,
      });
      setJob(data.job);
    } catch (error) {
      if (error.status === 409 && error.data && error.data.job) {
        const busy = error.data.job;
        if (window.confirm(`The TV is busy with ${busy.title}. Stop it and play this instead?`)) {
          await startPlayback(movieId, rendererId, true, startAt);
          return;
        }
        setJob(busy);
        return;
      }
      notice = error.message;
      render();
    }
  }

  async function control(action, position) {
    if (!job) return;
    try {
      const data = await api("POST", `/api/playback/${job.id}/control`, { action, position });
      setJob(data.job);
    } catch (error) {
      notice = error.message;
      render();
    }
  }

  async function cancelJob() {
    if (!job) return;
    try {
      const data = await api("POST", `/api/playback/${job.id}/cancel`, {});
      setJob(data.job);
    } catch (error) {
      notice = error.message;
      render();
    }
  }

  function dismiss() {
    job = null;
    notice = "";
    render();
  }

  function setJob(next) {
    const finished = next && !next.active && (!job || job.active);
    job = next;
    if (finished && panelMovie && next.movie_id === panelMovie.id) loadResume(panelMovie.id);
    if (finished) window.dispatchEvent(new Event("movie-world-progress"));
    render();
    clearTimeout(pollTimer);
    if (job && job.active) pollTimer = setTimeout(poll, POLL_MS);
  }

  async function poll() {
    if (!job) return;
    try {
      const data = await api("GET", `/api/playback/${job.id}`);
      setJob(data.job);
    } catch (error) {
      if (error.status === 401) {
        setJob(null);
        return;
      }
      pollTimer = setTimeout(poll, POLL_MS * 2);
    }
  }

  async function refreshActive() {
    try {
      const data = await api("GET", "/api/playback/active");
      if (data.job) setJob(data.job);
    } catch {
      // No active playback, or the server is unavailable.
    }
  }

  async function mount(actions, movie) {
    const current = await loadSession();
    if (!current.enabled || !actions.isConnected) return;
    panelMovie = movie;
    resumePoint = null;
    notice = "";
    panel = el("div", "tv-panel");
    actions.insertAdjacentElement("afterend", panel);
    renderPanel();
    loadRenderers(false);
    loadResume(movie.id);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && session && session.enabled) {
      window.dispatchEvent(new Event("movie-world-progress"));
      if (job && job.active) poll();
      else refreshActive();
    }
  });

  loadSession().then((current) => {
    if (current.enabled) {
      refreshActive();
      window.dispatchEvent(new Event("movie-world-progress"));
    }
  });

  window.MovieWorldTv = { mount };
})();
