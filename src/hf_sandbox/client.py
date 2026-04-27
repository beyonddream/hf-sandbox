"""HFSandbox client. Use from the master process."""

import re
import secrets
import socket
import time
from pathlib import Path

import dns.resolver
import httpx
from huggingface_hub import cancel_job, fetch_job_logs, get_token, run_job

# Some local resolvers (e.g. systemd-resolved) return NXDOMAIN for fresh
# trycloudflare.com subdomains even though public DNS resolves them fine.
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
    resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
    resolver.timeout = 5
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _HOST_OVERRIDES[hostname] = str(resolver.resolve(hostname, "A")[0])
            return
        except dns.resolver.NXDOMAIN:
            time.sleep(2)
    raise TimeoutError(f"DNS for {hostname} never propagated within {timeout}s")

_SERVER_SRC = (Path(__file__).parent / "server.py").read_text()

_BOOTSTRAP = f"""set -e
pip install -q fastapi uvicorn
python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', '/tmp/cf')"
chmod +x /tmp/cf
cat > /tmp/server.py << 'PYEOF'
{_SERVER_SRC}
PYEOF
python -u /tmp/server.py &
exec /tmp/cf tunnel --url http://localhost:8000 --no-autoupdate 2>&1
"""

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class HFSandbox:
    def __init__(self, job_id: str, url: str, token: str):
        self.job_id = job_id
        self.url = url
        self._http = httpx.Client(headers={"Authorization": f"Bearer {token}"})

    @classmethod
    def create(cls, image: str, flavor: str = "cpu-basic", timeout: str = "1h"):
        token = secrets.token_urlsafe(32)
        job = run_job(
            image=image,
            command=["bash", "-c", _BOOTSTRAP],
            env={"HF_SANDBOX_TOKEN": token},
            secrets={"HF_TOKEN": get_token()},
            flavor=flavor,
            timeout=timeout,
        )
        url = cls._wait_for_url(job.id)
        _register_public_dns_override(url.split("://", 1)[1].split("/", 1)[0])
        sb = cls(job.id, url, token)
        sb._wait_healthy()
        return sb

    @staticmethod
    def _wait_for_url(job_id: str, timeout: float = 300) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for line in fetch_job_logs(job_id=job_id, follow=False):
                m = _URL_RE.search(line)
                if m:
                    return m.group(0)
            time.sleep(2)
        raise TimeoutError(f"tunnel URL never appeared in logs for job {job_id}")

    def _wait_healthy(self, timeout: float = 60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._http.get(f"{self.url}/health", timeout=5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise TimeoutError(f"sandbox at {self.url} never became healthy")

    def exec(self, cmd: list[str], cwd: str | None = None, stdin: str | None = None,
             timeout: int = 600) -> dict:
        return self._http.post(
            f"{self.url}/exec",
            json={"cmd": cmd, "cwd": cwd, "stdin": stdin, "timeout": timeout},
            timeout=timeout + 10,
        ).json()

    def write_file(self, path: str, content: str):
        self._http.post(f"{self.url}/write", json={"path": path, "content": content})

    def read_file(self, path: str) -> str:
        return self._http.post(f"{self.url}/read", json={"path": path}).json()["content"]

    def terminate(self):
        self._http.close()
        cancel_job(job_id=self.job_id)
