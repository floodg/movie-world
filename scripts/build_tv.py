"""Generate the Samsung RU7100 (2019 / Chromium 63) frontend. Stdlib only."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
TARGET = WEB / "tv"


def build():
    TARGET.mkdir(exist_ok=True)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('  <script src="/tvcast.js"></script>\n', '')
    html = html.replace('/styles.css', '/tv/styles.css').replace('/app.js', '/tv/app.js')
    html = html.replace('<script src="/tv/app.js">', '<script src="/tv/compat.js"></script>\n  <script src="/tv/app.js">')
    html = html.replace('<title>Movie World</title>', '<title>Movie World TV</title>')
    (TARGET / "index.html").write_text(html, encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    js = js.replace('catch {', 'catch (error) {')
    js = js.replace('hsl(${hue(genre.name)} 42% 32%)', 'hsl(${hue(genre.name)}, 42%, 32%)')
    js = re.sub(r'movie\.(\w+) \?\? null', r'(movie.\1 == null ? null : movie.\1)', js)
    # Object spread arrived after Chromium 63.
    js = js.replace('[favouriteSnapshot(movie), ...favourites]', '[favouriteSnapshot(movie)].concat(favourites)')
    js = js.replace('scroller.scrollBy({ left: -scroller.clientWidth * 0.8, behavior: "smooth" });', 'scroller.scrollLeft -= scroller.clientWidth * 0.8;')
    js = js.replace('scroller.scrollBy({ left: scroller.clientWidth * 0.8, behavior: "smooth" });', 'scroller.scrollLeft += scroller.clientWidth * 0.8;')
    js = js.replace('window.scrollTo({\n      top: document.documentElement.scrollHeight,\n      behavior: "smooth",\n    });', 'railEl.scrollIntoView(false);')
    js = re.sub(r'\.scrollIntoView\(\{[^}]+\}\)', '.scrollIntoView(false)', js)
    js = js.replace('localStorage.setItem(FAVOURITES_KEY, JSON.stringify(favourites));', 'try { localStorage.setItem(FAVOURITES_KEY, JSON.stringify(favourites)); } catch (error) { /* Keep session favourites if storage is unavailable. */ }')
    start = js.index('async function fetchJson(')
    end = js.index('\nfunction renderGenres()', start)
    js = js[:start] + '''function fetchJson(url, signal) {
  return window.tvRequest(url, signal);
}
''' + js[end:]
    (TARGET / "app.js").write_text(js, encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    # Do not put :focus beside unsupported :focus-visible in the same rule.
    css = css.replace(':focus-visible', ':focus')
    css = css.replace('inset: 0;', 'top: 0; right: 0; bottom: 0; left: 0;')
    css = css.replace('width: min(760px, calc(100vw - 32px));', 'width: calc(100vw - 32px); max-width: 760px;')
    css += '''
/* Chromium 63 has Grid but does not implement gap for flex containers. */
[hidden] { display: none !important; }
.top > * + *, .top-actions > * + * { margin-left: 16px; }
.search-fields > * + * { margin-left: 10px; }
.rail-nav > * + * { margin-left: 8px; }
.scroller > .card { margin-right: 12px; flex-shrink: 0; }
.favourites-grid > .card, .results-grid > .card { margin: 0 12px 12px 0; }
.results-grid { display: flex; flex-wrap: wrap; }
.actor-list > * + * { margin-top: 8px; }
.detail-meta > * { margin: 0 14px 8px 0; }
.detail-actions > * { margin: 0 10px 10px 0; }
.fav-action svg, .fav-link span { margin-right: 8px; }
button:focus, a:focus { outline: 3px solid white; outline-offset: 3px; }
body.tv-modal-open { overflow: hidden; }
body.tv-modal-open::before { content: ""; position: fixed; top: 0; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,.72); z-index: 99; }
#detail[open] { display: block; position: fixed; top: 16px; left: 0; right: 0; margin: 0 auto; z-index: 100; }
#detail:not([open]) { display: none; }
@media (max-width: 640px) {
  .top > * + *, .search-fields > * + * { margin-left: 0; margin-top: 12px; }
}
'''
    (TARGET / "styles.css").write_text(css, encoding="utf-8")


if __name__ == "__main__":
    build()
