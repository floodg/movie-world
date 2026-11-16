(() => {
  const $ = id => document.getElementById(id);
  let next = null, generation = 0, timer;
  const rows = new Set();
  function element(tag, text, className) {
    const node = document.createElement(tag); node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  async function load(older = false) {
    const version = ++generation;
    $('refresh').disabled = $('older').disabled = true;
    const params = new URLSearchParams({source:$('source').value,level:$('level').value,q:$('query').value});
    if (older && next) params.set('before', next);
    $('status').textContent = 'Loading history…';
    try {
      const response = await fetch('/api/operations?' + params);
      if (!response.ok) throw new Error(`History unavailable (HTTP ${response.status}). Try again.`);
      const result = await response.json();
      if (version !== generation) return;
      if (!older) { $('events').replaceChildren(); rows.clear(); }
      for (const row of result.events) {
        if (rows.has(row.event_id)) continue;
        rows.add(row.event_id);
        const article = element('article', '');
        const date = new Date(row.timestamp);
        const time = element('time', date.toLocaleString()); time.dateTime = date.toISOString();
        const body = element('div', ''), meta = element('div', '', 'meta');
        meta.append(element('span', row.level, 'badge ' + row.level), element('strong', row.source === 'server' ? 'PC server' : 'Android app'), element('span', row.operation));
        if (row.movie_id) meta.append(element('span', 'Movie #' + row.movie_id));
        body.append(meta, element('p', row.message, 'message')); article.append(time, body); $('events').append(article);
      }
      next = result.next; $('older').hidden = !next;
      $('status').textContent = rows.size ? `${rows.size} events shown · updated ${new Date().toLocaleTimeString()}` : 'No matching operations yet. New activity will appear here.';
    } catch (error) { if (version === generation) $('status').textContent = error.message; }
    finally { if (version === generation) $('refresh').disabled = $('older').disabled = false; }
  }
  $('refresh').onclick = () => load(); $('older').onclick = () => load(true);
  for (const id of ['source','level']) $(id).onchange = () => load();
  $('query').oninput = () => { clearTimeout(timer); timer = setTimeout(() => load(), 300); };
  load();
})();
