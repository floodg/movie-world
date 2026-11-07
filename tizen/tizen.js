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
  surface.style.cssText = 'position:fixed;left:0;top:0;width:100%;height:100%;z-index:-1';
  document.body.appendChild(surface);
  var readyResolve;
  var ready = new Promise(function (resolve) { readyResolve = resolve; });
  var status = document.createElement('div');
  status.style.cssText = 'position:fixed;bottom:25px;left:30px;z-index:10002;background:#142238;color:white;padding:14px;font-size:24px';
  status.hidden = true;
  document.body.appendChild(status);
  function message(text) { status.textContent = text; status.hidden = !text; }
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
    var detail = document.getElementById('detail');
    if (detail.hasAttribute('open') && detail.close) detail.close();
    var panel = document.createElement('form');
    panel.style.cssText = 'position:fixed;top:50px;right:50px;bottom:50px;left:50px;z-index:10010;background:#142238;color:white;padding:45px;font-size:28px';
    panel.innerHTML = '<h1>Connect to Movie World PC</h1><p>PC address (for example http://192.168.0.20)</p><input id="mw-host" style="width:90%"><p>Pairing token from scripts/pair_tizen.py</p><input id="mw-secret" type="password" style="width:90%"><p><button>Save and connect</button></p><p id="mw-error"></p>';
    document.body.appendChild(panel);
    panel.querySelector('#mw-host').value = base;
    panel.querySelector('#mw-secret').value = token;
    panel.onsubmit = function (event) {
      event.preventDefault();
      var address = panel.querySelector('#mw-host').value.trim().replace(/\/$/, '');
      var secret = panel.querySelector('#mw-secret').value.trim();
      if (!/^http:\/\/(192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$/.test(address) || !/^[a-f0-9]{32}$/.test(secret)) {
        panel.querySelector('#mw-error').textContent = 'Enter a private LAN HTTP address and the 32-character pairing token.'; return;
      }
      base = address; token = secret;
      localStorage.setItem('mw-pc', base); localStorage.setItem('mw-token', token);
      panel.remove(); readyResolve(); location.reload();
    };
    panel.querySelector('input').focus();
  }
  var settingsButton = document.createElement('button');
  settingsButton.textContent = 'PC Settings';
  settingsButton.onclick = settings;
  document.querySelector('.top').appendChild(settingsButton);
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
    return request('POST', '/api/tizen/' + job.id + '/progress', {state:state, position:position, duration:duration});
  }
  function close(state) {
    generation++; opening = false;
    clearInterval(timer); clearTimeout(polling);
    if (job) {
      var pending = job.media_url ? report(state || 'stopped') : request('POST', '/api/tizen/' + job.id + '/cancel');
      pending.catch(function (e) { message(e.message); });
    }
    job = null;
    try { if (av) av.close(); } catch (e) { /* Already closed. */ }
    document.body.classList.remove('mw-playing');
    if (document.getElementById('detail').hasAttribute('open')) {
      var button = document.getElementById('detail').querySelector('button');
      if (button) button.focus();
    }
    message('');
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
          if (current.start_at) av.seekTo(Math.min(current.start_at * 1000, Math.max(0, av.getDuration() - 5000)));
          document.body.classList.add('mw-playing'); message('');
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
  window.MovieWorldTv = {mount: function (actions, movie) {
    var button = document.createElement('button');
    button.textContent = 'Play on this TV';
    button.onclick = function () {
      if (job || opening) { message('Stop the current movie with Back first.'); return; }
      opening = true; message('Preparing movie…');
      var version = ++generation;
      request('POST', '/api/tizen/prepare', {movie_id:movie.id, idempotency_key:'tv_' + Date.now()}).then(function (data) {
        if (version !== generation) { request('POST', '/api/tizen/' + data.job.id + '/cancel').catch(function () {}); return; }
        job = data.job; opening = false; poll();
      }).catch(function (e) { opening = false; message(e.message); });
    };
    actions.appendChild(button);
  }};
  document.addEventListener('keydown', function (event) {
    var key = event.keyCode;
    if (!job && !opening) {
      if (key === 10009 && !document.getElementById('detail').hasAttribute('open')) {
        event.preventDefault(); tizen.application.getCurrentApplication().exit();
      }
      return;
    }
    if ([10009, 27, 13, 415, 19, 10252, 413, 37, 39, 412, 417].indexOf(key) < 0) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (key === 10009 || key === 27 || key === 413) { close(); return; }
    if (!job || !job.media_url || opening) return;
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
  try { ['MediaPlay', 'MediaPause', 'MediaPlayPause', 'MediaStop', 'MediaRewind', 'MediaFastForward'].forEach(function (key) { tizen.tvinputdevice.registerKey(key); }); } catch (e) { /* Browser preview. */ }
  var style = document.createElement('style');
  style.textContent = 'body.mw-playing{background:transparent!important}body.mw-playing>header,body.mw-playing>main,body.mw-playing>dialog,body.mw-playing>footer{visibility:hidden!important}';
  document.head.appendChild(style);
}());
