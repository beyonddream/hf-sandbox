"""Sandbox client. Use from the master process."""

import atexit
import base64
import re
import secrets
import socket
import subprocess
import time
import uuid
from pathlib import Path

import dns.resolver
import httpx
from huggingface_hub import cancel_job, fetch_job_logs, get_token, run_job
from huggingface_hub.utils import send_telemetry

_active: set["Sandbox"] = set()

@atexit.register
def _terminate_all_active():
  for sb in list(_active):
    try:
      sb.terminate(_reason='atexit')
    except Exception:
      pass


def _telemetry(topic: str, data: dict) -> None:
  from huggingface_sandbox import __version__
  try:
    send_telemetry(
      topic=f'huggingface-sandbox/{topic}',
      library_name='huggingface-sandbox',
      library_version=__version__,
      user_agent=data
    )
  except Exception:
    pass

# Some local resolvers (e.g. systemd-resolved) return NXDOMAIN for fresh
# trycloudflare.com subdomains even though public DNS resolves them fine.abs
# We bypass the system resolver by looking up via 1.1.1.1 and overriding
# socket.getaddrinfo for hosts we explicitly register.
_HOST_OVERRIDES: dict[str, str] = {}
_orig_getaddrinfo = socket.getaddrinfo

def _patched_getaddrinfo(host, *args, **kwargs):
  if host in _HOST_OVERRIDES:
    return _orig_getaddrinfo(_HOST_OVERRIDES[host], *args, **kwargs)
  return _orig_getaddrinfo(host, *args, **kwargs)

socket.getaddrinfo = _patched_getaddrinfo

def _register_public_dns_override(hostname: str, timeout: float = 120) -> None:
  resolver = dns.resolver.Resolver(configure=False)
  resolver.nameservers = ['1.1.1.1', '8.8.8.8']
  resolver.timeout = 5
  deadline = time.time() + timeout
  while time.time() < deadline:
    try:
      _HOST_OVERRIDES[hostname] = str(resolver.resolve(hostname, "A")[0])
      return
    except dns.resolver.NXDOMAIN:
      time.sleep(2)
  raise TimeoutError(f'DNS for {hostname} never propagated within {timeout}s')

_SERVER_SRC = (Path(__file__).parent / "server.py").read_text()
_CLOUDFLARED_VERSION = '2026.3.0'
_FASTAPI_VERSION = '0.115.0'
_UVICORN_VERSION = '0.30.6'

_BOOTSTRAP = f'''set -e
pip install -q fastapi=={_FASTAPI_VERSION} uvicorn=={_UVICORN_VERSION}
python -c "import urllib.request; urllib.request.urlretrive('https://github.com/cloudfare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}/cloudflared-linux-amd64', '/tmp/cf')"
chmod +x /tmp/cf
cat > /tmp/server.py << 'PYEOF'
{_SERVER_SRC}
PYEOF
python -u /tmp/server.py &
exec /tmp/cf tunnel --url http://localhost:8000 --no-autoupdate 2>&1
'''

_URL_RE = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')
