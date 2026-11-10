/* Samsung Tizen 5.0. Keep transport and player independent of the catalogue UI. */
(function () {
  'use strict';
  if (!Promise.allSettled) Promise.allSettled = function (values) {
    return Promise.all(Array.from(values).map(function (value) {
      return Promise.resolve(value).then(function (result) { return {status:'fulfilled', value:result}; },
        function (reason) { return {status:'rejected', reason:reason}; });
    }));
  };
  if (!Array.prototype.flatMap) Object.defineProperty(Array.prototype, 'flatMap', {value:function (fn, context) {
    return Array.prototype.concat.apply([], this.map(fn, context));
  }, configurable:true, writable:true});
  var base = localStorage.getItem('mw-pc') || '';
  var token = localStorage.getItem('mw-token') || '';
  var job = null, timer = null, polling = null, opening = false, generation = 0;
  var av = window.webapis && window.webapis.avplay;
  var surface = document.createElement('object');
  surface.type = 'application/avplayer';
  surface.className = 'av-surface';
  surface.style.cssText = 'position:fixed;left:0;top:0;width:100%;height:100%;z-index:0';
  document.body.appendChild(surface);
  var readyResolve;
  var ready = new Promise(function (resolve) { readyResolve = resolve; });
  var status = document.createElement('div');
  status.className = 'player-status';
  status.setAttribute('role', 'status');
  var statusTitle = document.createElement('strong');
  var statusText = document.createElement('span');
  var statusStop = document.createElement('button');
  statusStop.textContent = 'Stop'; statusStop.onclick = function () { close(); };
  status.appendChild(statusTitle); status.appendChild(statusText); status.appendChild(statusStop);
  status.hidden = true;
  document.body.appendChild(status);
  var subtitleTracks = [], subtitleIndex = -1, subtitleDelay = 0;
  var subtitlePanel = document.createElement('section');
  subtitlePanel.className = 'subtitle-panel'; subtitlePanel.hidden = true;
  subtitlePanel.setAttribute('role', 'dialog'); subtitlePanel.setAttribute('aria-label', 'Subtitles');
  document.body.appendChild(subtitlePanel);
  function hideSubtitlesMenu() { subtitlePanel.hidden = true; }
  function subtitleButton(label, action) {
    var button = document.createElement('button'); button.textContent = label; button.onclick = action;
    subtitlePanel.appendChild(button); return button;
  }
  function selectSubtitle(index) {
    try {
      if (index >= 0) av.setSelectTrack('TEXT', index);
      av.setSilentSubtitle(index < 0);
      subtitleIndex = index;
      localStorage.setItem('mw-subtitles', index < 0 ? 'off' : 'on');
      showSubtitlesMenu();
    } catch (e) { subtitleNotice.textContent = 'This subtitle track is unavailable: ' + e.message; }
  }
  var subtitleNotice;
  function showSubtitlesMenu() {
    subtitlePanel.textContent = '';
    var heading = document.createElement('h2'); heading.textContent = 'Subtitles'; subtitlePanel.appendChild(heading);
    var selected = subtitleButton((subtitleIndex < 0 ? '✓ ' : '') + 'Off', function () { selectSubtitle(-1); });
    subtitleTracks.forEach(function (track) {
      var info = track.extra_info || {};
      if (typeof info === 'string') { try { info = JSON.parse(info); } catch (e) { info = {}; } }
      var label = info.track_lang || info.language || ('Track ' + (track.index + 1));
      var button = subtitleButton((track.index === subtitleIndex ? '✓ ' : '') + label, function () { selectSubtitle(track.index); });
      if (track.index === subtitleIndex) selected = button;
    });
    subtitleNotice = document.createElement('p'); subtitleNotice.setAttribute('role', 'status');
    subtitleNotice.textContent = subtitleTracks.length ? 'Timing: ' + (subtitleDelay / 1000).toFixed(1) + ' seconds' : 'No embedded subtitles available in this movie.';
    subtitlePanel.appendChild(subtitleNotice);
    function adjust(delta) {
      try {
        var next = Math.max(-30000, Math.min(30000, subtitleDelay + delta));
        av.setSubtitlePosition(next); subtitleDelay = next;
        subtitleNotice.textContent = 'Timing: ' + (subtitleDelay / 1000).toFixed(1) + ' seconds';
      } catch (e) { subtitleNotice.textContent = 'Timing adjustment is unavailable for this track.'; }
    }
    if (subtitleIndex >= 0) {
      subtitleButton('Show 0.5s earlier', function () { adjust(-500); });
      subtitleButton('Show 0.5s later', function () { adjust(500); });
    }
    subtitleButton('Close', hideSubtitlesMenu);
    subtitlePanel.hidden = false; selected.focus();
  }
  function initialiseSubtitles() {
    subtitleTracks = []; subtitleIndex = -1; subtitleDelay = 0;
    try {
      subtitleTracks = av.getTotalTrackInfo().filter(function (track) { return track.type === 'TEXT'; });
      if (localStorage.getItem('mw-subtitles') === 'on' && subtitleTracks.length) {
        var english = subtitleTracks.filter(function (track) { return /\b(en|eng|english)\b/i.test(JSON.stringify(track.extra_info)); })[0];
        subtitleIndex = (english || subtitleTracks[0]).index;
        av.setSelectTrack('TEXT', subtitleIndex);
      }
      av.setSilentSubtitle(subtitleIndex < 0);
    } catch (e) { /* Unsupported subtitle APIs must not prevent the movie playing. */ }
  }
  function message(text) { statusText.textContent = text; status.hidden = !text; statusStop.hidden = !job && !opening; }
  function request(method, path, data) {
    return ready.then(function () {
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open(method, base + path);
        xhr.timeout = 30000;
        if (method === 'POST') {
          xhr.setRequestHeader('Content-Type', 'application/json');
          xhr.setRequestHeader('X-Movie-World-Token', token);
        }
        xhr.onload = function () {
          var result;
          try { result = JSON.parse(xhr.responseText); } catch (e) { reject(new Error('Invalid PC response')); return; }
          if (xhr.status < 200 || xhr.status >= 300) reject(new Error(result.error || 'PC request failed'));
          else resolve(result);
        };
        xhr.onerror = xhr.ontimeout = function () { reject(new Error('Cannot reach the PC. Check Settings and the network.')); };
        xhr.send(method === 'POST' ? JSON.stringify(data || {}) : null);
      });
    });
  }
  window.tvRequest = function (path, signal) {
    return request('GET', path).then(function (data) {
      if (signal && signal.aborted) throw new Error('Request cancelled');
      return data;
    });
  };
  function settings() {
    if (job || opening) close();
    var panel = document.getElementById('connection');
    panel.hidden = false;
    panel.querySelector('#mw-host').value = base;
    panel.querySelector('#mw-secret').value = token;
    document.getElementById('connection-form').onsubmit = function (event) {
      event.preventDefault();
      var address = panel.querySelector('#mw-host').value.trim().replace(/\/$/, '');
      var secret = panel.querySelector('#mw-secret').value.trim();
      if (!/^http:\/\/(192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$/.test(address) || !/^[0-9]{6}$/.test(secret)) {
        panel.querySelector('#mw-error').textContent = 'Enter a private LAN HTTP address and the 6-digit pairing code.'; return;
      }
      base = address; token = secret;
      localStorage.setItem('mw-pc', base); localStorage.setItem('mw-token', token);
      panel.hidden = true; readyResolve(); location.reload();
    };
    panel.querySelector('input').focus();
  }
  document.getElementById('settings-open').onclick = settings;
  document.getElementById('settings-close').onclick = function () {
    if (!base || !token) { document.getElementById('mw-error').textContent = 'Connect to the PC to browse your catalogue.'; return; }
    document.getElementById('connection').hidden = true;
    document.getElementById('settings-open').focus();
  };
  if (base && token) readyResolve(); else settings();

  function report(state) {
    if (!job) return Promise.resolve();
    var position = job.position || 0, duration = job.duration || 0;
    try {
      if (av && av.getState() !== 'NONE' && av.getState() !== 'IDLE') {
        position = av.getCurrentTime() / 1000; duration = av.getDuration() / 1000;
      }
    } catch (e) { /* Preserve the last known position. */ }
    job.position = position; job.duration = duration;
    window.dispatchEvent(new CustomEvent('mw-progress', {detail:{movie_id:job.movie_id,title:job.title,position:position,duration:duration,state:state}}));
    return request('POST', '/api/tizen/' + job.id + '/progress', {state:state, position:position, duration:duration});
  }
  function close(state) {
    hideSubtitlesMenu(); subtitleTracks = []; subtitleIndex = -1; subtitleDelay = 0;
    generation++; opening = false;
    clearInterval(timer); clearTimeout(polling);
    if (job) {
      var pending = job.media_url ? report(state || 'stopped') : request('POST', '/api/tizen/' + job.id + '/cancel');
      pending.catch(function (e) { message(e.message); });
    }
    job = null;
    try { if (av) av.close(); } catch (e) { /* Already closed. */ }
    document.body.classList.remove('mw-playing');
    if (!document.getElementById('detail').hidden) {
      var button = document.getElementById('detail').querySelector('button');
      if (button) button.focus();
    }
    message('');
    window.dispatchEvent(new CustomEvent('mw-playback-closed'));
  }
  function fail(error) { close('failed'); message('Playback failed: ' + (error.message || error)); }
  function play(current) {
    var version = generation;
    if (!av) { fail(new Error('Samsung AVPlay is unavailable on this device.')); return; }
    opening = true;
    try {
      av.open(current.media_url);
      av.setDisplayRect(0, 0, 1920, 1080);
      av.setListener({
        onbufferingstart: function () { message('Buffering…'); },
        onbufferingcomplete: function () { message(''); },
        onstreamcompleted: function () { close('ended'); },
        onerror: function (error) { if (version === generation) fail(error); }
      });
      av.prepareAsync(function () {
        if (version !== generation) return;
        try {
          av.play(); opening = false;
          initialiseSubtitles();
          if (current.start_at) av.seekTo(Math.min(current.start_at * 1000, Math.max(0, av.getDuration() - 5000)));
          document.body.classList.add('mw-playing'); message('Press Down for subtitles');
          setTimeout(function () { if (version === generation && statusText.textContent === 'Press Down for subtitles') message(''); }, 4000);
          report('playing').catch(fail);
          timer = setInterval(function () {
            report(av.getState() === 'PAUSED' ? 'paused' : 'playing').then(function (data) {
              if (version === generation && !data.job.active) close();
            }).catch(function (e) { if (version === generation) fail(e); });
          }, 10000);
        } catch (e) { fail(e); }
      }, function (error) { if (version === generation) fail(error); });
    } catch (e) { fail(e); }
  }
  function poll() {
    var version = generation;
    request('POST', '/api/tizen/' + job.id + '/status').then(function (data) {
      if (version !== generation || !job) return;
      job = data.job;
      if (!job.active) { fail(new Error((job.error || {}).message || job.detail)); return; }
      if (job.media_url) { play(job); return; }
      var progress = job.progress || {};
      message((job.detail || job.state) + (progress.total ? ' ' + Math.floor(progress.done / progress.total * 100) + '%' : ''));
      polling = setTimeout(poll, 1500);
    }).catch(function (e) { if (version === generation) fail(e); });
  }
  window.MovieWorldTv = {settings:settings, stop:close, play:function (movie, restart) {
      if (job || opening) { message('Stop the current movie with Back first.'); return; }
      statusTitle.textContent = movie.title;
      opening = true; message('Preparing movie…');
      var version = ++generation;
      var body = {movie_id:movie.id, idempotency_key:'tv_' + Date.now()};
      if (restart) body.start_at = 0;
      request('POST', '/api/tizen/prepare', body).then(function (data) {
        if (version !== generation) { request('POST', '/api/tizen/' + data.job.id + '/cancel').catch(function () {}); return; }
        job = data.job; opening = false; poll();
      }).catch(function (e) { opening = false; message(e.message); });
  }};
  document.addEventListener('keydown', function (event) {
    var key = event.keyCode;
    if (!job && !opening) {
      return;
    }
    if (!subtitlePanel.hidden) {
      if ([10009, 27, 13, 37, 38, 39, 40].indexOf(key) >= 0) {
        event.preventDefault(); event.stopImmediatePropagation();
        if (key === 10009 || key === 27) { hideSubtitlesMenu(); return; }
        if (key === 13) { document.activeElement.click(); return; }
        var buttons = Array.from(subtitlePanel.querySelectorAll('button'));
        var next = buttons.indexOf(document.activeElement) + ((key === 37 || key === 38) ? -1 : 1);
        buttons[(next + buttons.length) % buttons.length].focus(); return;
      }
    }
    if ([10009, 27, 13, 415, 19, 10252, 413, 37, 39, 40, 460, 412, 417].indexOf(key) < 0) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (key === 10009 || key === 27 || key === 413) { close(); return; }
    if (!job || !job.media_url || opening) return;
    if (key === 40 || key === 460) { showSubtitlesMenu(); return; }
    try {
      if (key === 37 || key === 39 || key === 412 || key === 417) {
        av.seekTo(Math.max(0, Math.min(av.getDuration() - 1000, av.getCurrentTime() + ((key === 37 || key === 412) ? -30000 : 30000))));
      } else {
        if (key === 19 || (key !== 415 && av.getState() === 'PLAYING')) av.pause(); else av.play();
        report(av.getState() === 'PAUSED' ? 'paused' : 'playing').catch(fail);
      }
    } catch (e) { message(e.message); }
  }, true);
  document.addEventListener('visibilitychange', function () { if (document.hidden) close(); });
  window.addEventListener('pagehide', function () { close(); });
  try { ['MediaPlay', 'MediaPause', 'MediaPlayPause', 'MediaStop', 'MediaRewind', 'MediaFastForward', 'Subtitle'].forEach(function (key) { tizen.tvinputdevice.registerKey(key); }); } catch (e) { /* Browser preview. */ }
}());
