"""Build an unsigned Tizen project in .local/tizen-app, using the TV compatibility build."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def build():
    # Generate in isolation so existing tracked TV assets are not overwritten.
    spec = importlib.util.spec_from_file_location("tv_builder", ROOT / "scripts/build_tv.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = ROOT / ".local/tizen-app"
    output.mkdir(parents=True, exist_ok=True)
    module.TARGET = output
    module.build()
    app = output / 'app.js'
    js = app.read_text(encoding='utf-8')
    start = js.index('  if (typeof movie.telegram_url === "string"')
    end = js.index('  body.appendChild(actions);', start)
    js = js[:start] + js[end:]
    app.write_text(js, encoding='utf-8')
    html = (output / "index.html").read_text(encoding="utf-8")
    html = html.replace('/tv/', './')
    html = html.replace('<script src="./compat.js"></script>', '<script src="$WEBAPIS/webapis/webapis.js"></script>\n  <script src="tizen.js"></script>\n  <script src="./compat.js"></script>')
    (output / "index.html").write_text(html, encoding="utf-8")
    compat = (output / 'compat.js')
    # build_tv does not copy the maintained compatibility layer.
    compat.write_text((ROOT / 'web/tv/compat.js').read_text(encoding='utf-8').replace(
        'window.tvRequest = function', 'window.tvRequest = window.tvRequest || function'), encoding='utf-8')
    for name in ("config.xml", "tizen.js"):
        shutil.copyfile(ROOT / "tizen" / name, output / name)
    compiler = ROOT / 'tizen/node_modules/esbuild/bin/esbuild'
    if not compiler.exists():
        raise SystemExit('Install the TV build dependency first: npm ci --prefix tizen')
    for name in ('app.js', 'compat.js', 'tizen.js'):
        source = output / name
        script = "const fs=require('fs'),esbuild=require(process.argv[1]);const p=process.argv[2];fs.writeFileSync(p,esbuild.transformSync(fs.readFileSync(p,'utf8'),{target:'chrome63'}).code);"
        subprocess.run(['node', '-e', script, str(ROOT / 'tizen/node_modules/esbuild'), str(source)], check=True)
    # Standard-library PNG icon, avoiding an extra build dependency.
    import struct, zlib
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + bytes([28, 55, 92]) * 117 for _ in range(117))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 117, 117, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
    (output / 'icon.png').write_bytes(png)
    print(output)

if __name__ == "__main__":
    build()
