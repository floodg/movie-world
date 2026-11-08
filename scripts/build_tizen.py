"""Compile the Kotlin-matched UI and launcher vector for Samsung Chromium 63."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    source = ROOT / 'tizen'
    output = ROOT / '.local/tizen-app'
    dependencies = source / 'node_modules'
    if not (dependencies / 'esbuild').exists():
        raise SystemExit('Install build dependencies first: npm ci --prefix tizen --ignore-scripts')
    output.mkdir(parents=True, exist_ok=True)
    for name in ('index.html', 'styles.css', 'app.js', 'tizen.js', 'config.xml', 'icon.svg'):
        shutil.copyfile(source / name, output / name)
    for name in ('app.js', 'tizen.js'):
        script = "const fs=require('fs'),esbuild=require(process.argv[1]);const p=process.argv[2];fs.writeFileSync(p,esbuild.transformSync(fs.readFileSync(p,'utf8'),{target:'chrome63'}).code);"
        subprocess.run(['node', '-e', script, str(dependencies / 'esbuild'), str(output / name)], check=True)
    script = "const fs=require('fs'),{Resvg}=require(process.argv[1]);fs.writeFileSync(process.argv[3],new Resvg(fs.readFileSync(process.argv[2]),{fitTo:{mode:'width',value:117}}).render().asPng());"
    subprocess.run(['node', '-e', script, str(dependencies / '@resvg/resvg-js'), str(source / 'icon.svg'), str(output / 'icon.png')], check=True)
    fonts = output / 'fonts'
    fonts.mkdir(exist_ok=True)
    for family, weights in (('roboto', (400, 500, 600)), ('roboto-condensed', (600, 700))):
        for weight in weights:
            filename = f'{family}-latin-{weight}-normal.woff2'
            shutil.copyfile(dependencies / '@fontsource' / family / 'files' / filename, fonts / filename)
        shutil.copyfile(dependencies / '@fontsource' / family / 'LICENSE', fonts / f'{family}-LICENSE.txt')
    old = output / 'compat.js'
    if old.exists():
        old.unlink()
    print(output)


if __name__ == '__main__':
    build()
