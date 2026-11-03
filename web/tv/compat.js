/* Samsung RU7100: Chromium 63. No CDN or modern syntax required to boot. */
(function () {
  'use strict';
  if (!Element.prototype.replaceChildren) {
    Element.prototype.replaceChildren = function () {
      while (this.firstChild) this.removeChild(this.firstChild);
      this.append.apply(this, arguments);
    };
  }
  // A small abort signal for our XHR transport; native AbortController is newer.
  window.AbortController = function () {
    this.signal = { aborted: false, cancel: null };
  };
  window.AbortController.prototype.abort = function () {
    this.signal.aborted = true;
    if (this.signal.cancel) this.signal.cancel();
  };
  window.tvRequest = function (url, signal) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      function abortError() {
        var error = new Error('Request cancelled');
        error.name = 'AbortError';
        reject(error);
      }
      if (signal && signal.aborted) { abortError(); return; }
      xhr.open('GET', url, true);
      xhr.timeout = 30000;
      xhr.onload = function () {
        if (signal) signal.cancel = null;
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error('Request failed (' + xhr.status + ')'));
          return;
        }
        try { resolve(JSON.parse(xhr.responseText)); } catch (error) { reject(error); }
      };
      xhr.onerror = xhr.ontimeout = function () { reject(new Error('Network request failed')); };
      xhr.onabort = abortError;
      if (signal) signal.cancel = function () { xhr.abort(); };
      xhr.send();
    });
  };

  var dialog = document.getElementById('detail');
  var previousFocus;
  // Use an ordinary overlay even when the TV exposes a partial dialog API.
  dialog.showModal = function () {
    previousFocus = document.activeElement;
    this.setAttribute('open', '');
    document.body.classList.add('tv-modal-open');
    this.setAttribute('tabindex', '-1');
    this.focus();
  };
  dialog.close = function () {
    this.removeAttribute('open');
    document.body.classList.remove('tv-modal-open');
    if (previousFocus && document.documentElement.contains(previousFocus)) previousFocus.focus();
  };
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  function controls() {
    var root = dialog.hasAttribute('open') ? dialog : document;
    return Array.prototype.filter.call(root.querySelectorAll('button, input, a[href]'), function (el) {
      return !el.disabled && el.getClientRects().length > 0;
    });
  }
  document.addEventListener('keydown', function (event) {
    var key = event.keyCode;
    var modal = dialog.hasAttribute('open');
    if ((key === 10009 || key === 27) && modal) {
      event.preventDefault(); event.stopImmediatePropagation(); dialog.close(); return;
    }
    var items = controls();
    var current = document.activeElement;
    if (key === 9 && modal) {
      var index = items.indexOf(current);
      event.preventDefault();
      if (items.length) items[(index + (event.shiftKey ? -1 : 1) + items.length) % items.length].focus();
      return;
    }
    // Left/right remain available for moving the cursor in TV text inputs.
    if (key < 37 || key > 40 || (current.tagName === 'INPUT' && (key === 37 || key === 39))) return;
    event.preventDefault();
    var box = current.getBoundingClientRect();
    var x = box.left + box.width / 2, y = box.top + box.height / 2;
    var best = null, score = Infinity;
    items.forEach(function (item) {
      if (item === current) return;
      var r = item.getBoundingClientRect();
      var dx = r.left + r.width / 2 - x, dy = r.top + r.height / 2 - y;
      var forward = key === 37 ? -dx : key === 39 ? dx : key === 38 ? -dy : dy;
      var side = key === 37 || key === 39 ? Math.abs(dy) : Math.abs(dx);
      if (forward <= 1) return;
      var distance = forward + side * 3;
      if (distance < score) { best = item; score = distance; }
    });
    if (!best && items.indexOf(current) < 0) best = items[0];
    if (best) { best.focus(); best.scrollIntoView(false); }
  }, true);
  // Asynchronous detail rendering should focus the Close button once ready.
  new MutationObserver(function () {
    if (dialog.hasAttribute('open') && !dialog.contains(document.activeElement)) {
      var button = dialog.querySelector('button');
      if (button) button.focus();
    } else if (document.activeElement === dialog) {
      var close = dialog.querySelector('button');
      if (close) close.focus();
    }
  }).observe(dialog, { childList: true });
}());
