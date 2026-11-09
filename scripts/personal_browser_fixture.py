"""Isolated HTTP fixture for PC browser sync tests; never connects to Telegram."""
import json
import sys
import tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from tvplay.personal import PersonalStore
with tempfile.TemporaryDirectory() as folder:
    class Handler(server.Handler):
        personal=PersonalStore(Path(folder)/"browser.sqlite")
        personal_sync=None
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    print(json.dumps({"port":http.server_port}),flush=True)
    try:http.serve_forever()
    finally:http.server_close()
