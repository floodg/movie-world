/**
 * Optional Supabase account sync for favourites and watch progress.
 * Loads only when web/cloud-config.js defines window.MOVIE_WORLD_CLOUD.
 * Catalogue, Telegram, and TV playback stay on the LAN server.
 */
(function () {
  const disabled = {
    enabled: false,
    async start() {},
    async syncFavourites() { return null; },
    async syncWatchProgress() { return null; },
    async pushFavourite() {},
    async pushWatchProgress() {},
    session() { return null; },
  };

  const config = window.MOVIE_WORLD_CLOUD;
  if (!config || !config.url || !config.publishableKey || !window.supabase?.createClient) {
    window.MovieWorldCloud = disabled;
    return;
  }

  const { createClient } = window.supabase;
  const client = createClient(config.url, config.publishableKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });

  let session = null;
  let profile = null;
  let syncing = false;
  let statusMessage = "Signed out";

  const accountRoot = () => document.getElementById("account");
  const statusEl = () => document.getElementById("account-status");
  const formEl = () => document.getElementById("account-form");
  const signedEl = () => document.getElementById("account-signed");
  const nameEl = () => document.getElementById("account-display-name");
  const emailEl = () => document.getElementById("account-email");
  const passwordEl = () => document.getElementById("account-password");
  const displayInput = () => document.getElementById("account-name-input");
  const displayEdit = () => document.getElementById("account-name-edit");

  function setStatus(message) {
    statusMessage = message;
    const el = statusEl();
    if (el) el.textContent = message;
  }

  function renderAccount() {
    const root = accountRoot();
    if (!root) return;
    root.hidden = false;
    const signedIn = Boolean(session?.user);
    if (formEl()) formEl().hidden = signedIn;
    if (signedEl()) signedEl().hidden = !signedIn;
    if (nameEl()) nameEl().textContent = profile?.display_name || session?.user?.email || "";
    if (signedIn && displayEdit()) displayEdit().value = profile?.display_name || "";
    if (!signedIn) setStatus("Signed out · favourites stay on this browser until you sign in");
  }

  async function loadProfile() {
    if (!session?.user) {
      profile = null;
      return;
    }
    const { data, error } = await client
      .from("profiles")
      .select("id, display_name, updated_at")
      .eq("id", session.user.id)
      .maybeSingle();
    if (error) throw error;
    profile = data;
  }

  async function saveDisplayName(value) {
    if (!session?.user) return;
    const display_name = String(value || "").trim() || null;
    const updated_at = new Date().toISOString();
    const { data, error } = await client
      .from("profiles")
      .update({ display_name, updated_at })
      .eq("id", session.user.id)
      .select("id, display_name, updated_at")
      .single();
    if (error) throw error;
    profile = data;
    renderAccount();
  }

  async function signUp(email, password, displayName) {
    const { data, error } = await client.auth.signUp({
      email,
      password,
      options: { data: { display_name: displayName || undefined } },
    });
    if (error) throw error;
    session = data.session;
    if (!session) {
      setStatus("Check your email to confirm the account, then sign in");
      return;
    }
    await afterAuth();
  }

  async function signIn(email, password) {
    const { data, error } = await client.auth.signInWithPassword({ email, password });
    if (error) throw error;
    session = data.session;
    await afterAuth();
  }

  async function signOut() {
    await client.auth.signOut();
    session = null;
    profile = null;
    renderAccount();
    setStatus("Signed out");
  }

  async function afterAuth() {
    await loadProfile();
    renderAccount();
    setStatus("Signed in · syncing…");
    await reconcile();
  }

  function isoNow() {
    return new Date().toISOString();
  }

  function toEpoch(value) {
    if (value == null) return 0;
    if (typeof value === "number") return value;
    const ms = Date.parse(value);
    return Number.isFinite(ms) ? ms / 1000 : 0;
  }

  async function pullFavourites() {
    const { data, error } = await client
      .from("favourites")
      .select("movie_id, removed_at, updated_at");
    if (error) throw error;
    return data || [];
  }

  async function pullWatchProgress() {
    const { data, error } = await client
      .from("watch_progress")
      .select("movie_id, position_seconds, duration_seconds, finished, updated_at");
    if (error) throw error;
    return data || [];
  }

  async function upsertFavouriteRow(movieId, removedAt, updatedAt) {
    const { error } = await client.from("favourites").upsert(
      {
        user_id: session.user.id,
        movie_id: Number(movieId),
        removed_at: removedAt,
        updated_at: updatedAt,
      },
      { onConflict: "user_id,movie_id" }
    );
    if (error) throw error;
  }

  async function upsertWatchRow(row) {
    const { error } = await client.from("watch_progress").upsert(
      {
        user_id: session.user.id,
        movie_id: Number(row.movie_id),
        position_seconds: Math.max(0, Math.floor(row.position_seconds || 0)),
        duration_seconds: row.duration_seconds == null ? null : Math.max(0, Math.floor(row.duration_seconds)),
        finished: Boolean(row.finished),
        updated_at: row.updated_at,
      },
      { onConflict: "user_id,movie_id" }
    );
    if (error) throw error;
  }

  async function reconcile() {
    if (!session?.user || syncing) return;
    syncing = true;
    try {
      if (typeof window.MovieWorldSync?.mergeFavouritesFromCloud === "function") {
        const cloudFavs = await pullFavourites();
        await window.MovieWorldSync.mergeFavouritesFromCloud(cloudFavs);
        const localFavs = window.MovieWorldSync.exportFavouritesForCloud();
        for (const row of localFavs) {
          const cloud = cloudFavs.find((item) => Number(item.movie_id) === Number(row.movie_id));
          if (!cloud || toEpoch(row.updated_at) >= toEpoch(cloud.updated_at)) {
            await upsertFavouriteRow(row.movie_id, row.removed_at, row.updated_at);
          }
        }
      }
      if (typeof window.MovieWorldSync?.mergeWatchFromCloud === "function") {
        const cloudWatch = await pullWatchProgress();
        await window.MovieWorldSync.mergeWatchFromCloud(cloudWatch);
        const localWatch = window.MovieWorldSync.exportWatchForCloud();
        for (const row of localWatch) {
          const cloud = cloudWatch.find((item) => Number(item.movie_id) === Number(row.movie_id));
          if (!cloud || toEpoch(row.updated_at) >= toEpoch(cloud.updated_at)) {
            await upsertWatchRow(row);
          }
        }
      }
      setStatus(navigator.onLine ? "Synced" : "Signed in · offline");
    } catch (error) {
      console.warn("Movie World cloud sync failed", error);
      setStatus(error.message || "Sync failed · local copy kept");
    } finally {
      syncing = false;
      renderAccount();
    }
  }

  function bindUi() {
    const root = accountRoot();
    if (!root || root.dataset.bound) return;
    root.dataset.bound = "1";
    root.hidden = false;

    document.getElementById("account-toggle")?.addEventListener("click", () => {
      const panel = document.getElementById("account-panel");
      if (!panel) return;
      panel.hidden = !panel.hidden;
    });

    document.getElementById("account-sign-in")?.addEventListener("click", async () => {
      try {
        setStatus("Signing in…");
        await signIn(emailEl()?.value.trim(), passwordEl()?.value);
      } catch (error) {
        setStatus(error.message || "Sign-in failed");
      }
    });

    document.getElementById("account-sign-up")?.addEventListener("click", async () => {
      try {
        setStatus("Creating account…");
        await signUp(emailEl()?.value.trim(), passwordEl()?.value, displayInput()?.value.trim());
      } catch (error) {
        setStatus(error.message || "Sign-up failed");
      }
    });

    document.getElementById("account-sign-out")?.addEventListener("click", async () => {
      try {
        await signOut();
      } catch (error) {
        setStatus(error.message || "Sign-out failed");
      }
    });

    document.getElementById("account-save-name")?.addEventListener("click", async () => {
      try {
        await saveDisplayName(displayEdit()?.value);
        setStatus("Display name saved");
      } catch (error) {
        setStatus(error.message || "Could not save display name");
      }
    });
  }

  window.MovieWorldCloud = {
    enabled: true,
    session() { return session; },
    async start() {
      bindUi();
      const { data } = await client.auth.getSession();
      session = data.session;
      client.auth.onAuthStateChange(async (_event, next) => {
        session = next;
        if (session) await afterAuth();
        else {
          profile = null;
          renderAccount();
        }
      });
      if (session) await afterAuth();
      else renderAccount();
      window.addEventListener("online", () => { if (session) reconcile(); });
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && session) reconcile();
      });
    },
    async syncFavourites() {
      if (!session) return null;
      await reconcile();
      return pullFavourites();
    },
    async syncWatchProgress() {
      if (!session) return null;
      await reconcile();
      return pullWatchProgress();
    },
    async pushFavourite(movieId, removedAt, updatedAt) {
      if (!session) return;
      try {
        await upsertFavouriteRow(movieId, removedAt, updatedAt || isoNow());
        setStatus("Synced");
      } catch (error) {
        setStatus(error.message || "Favourite will sync when online");
      }
    },
    async pushWatchProgress(row) {
      if (!session) return;
      try {
        await upsertWatchRow({
          ...row,
          updated_at: row.updated_at || isoNow(),
        });
        setStatus("Synced");
      } catch (error) {
        setStatus(error.message || "Watch progress will sync when online");
      }
    },
  };
})();
