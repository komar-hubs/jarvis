"""Call the running Jarvis from a console: python tools/jrpc.py <method> '<json params>'"""
import json
import sys
import urllib.request
from pathlib import Path

cfg = json.loads((Path(__file__).resolve().parent.parent / "data" / "config.json").read_text("utf-8"))
method = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
req = urllib.request.Request(f"http://127.0.0.1:{cfg.get('port', 8765)}/api/rpc",
                             data=json.dumps({"method": method, "params": params}).encode(),
                             headers={"Content-Type": "application/json", "X-Jarvis-Token": cfg["token"]})
print(json.dumps(json.loads(urllib.request.urlopen(req, timeout=60).read()), ensure_ascii=False, indent=1)[:4000])
