"""Create or revoke the private TV app token. Run locally; never commit the output."""
import argparse
import secrets
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--rotate', action='store_true', help='Revoke the previous token')
args = parser.parse_args()
path = Path(__file__).resolve().parents[1] / '.local/tizen-token'
path.parent.mkdir(exist_ok=True)
if args.rotate or not path.exists() or not re.fullmatch(r'[0-9]{6}', path.read_text().strip()):
    path.write_text(f'{secrets.randbelow(1000000):06d}', encoding='ascii')
print('Enter this 6-digit pairing code in Movie World TV settings:')
print(path.read_text().strip())
