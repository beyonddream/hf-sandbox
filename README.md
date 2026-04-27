# hf-sandbox

Modal-style sandbox API on top of Hugging Face Jobs.

```python
from hf_sandbox import HFSandbox

sb = HFSandbox.create(image="python:3.12")
print(sb.exec(["python", "-c", "print(1+1)"]))   # → {'rc': 0, 'stdout': '2\n', 'stderr': ''}
sb.write_file("/tmp/foo.txt", "hello")
print(sb.read_file("/tmp/foo.txt"))              # → 'hello'
sb.terminate()
```

## How it works

`HFSandbox.create()` launches an HF Job that:

1. `pip install`s a tiny FastAPI RPC server (FastAPI + uvicorn)
2. starts the server on `localhost:8000`
3. opens a free [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) so the master process can reach it

The client polls the job logs for the tunnel URL, then talks to the sandbox over plain HTTPS.

`exec`, `write_file`, `read_file` are simple authenticated POSTs.
`terminate()` cancels the job.

## Install

```bash
pip install git+https://github.com/<your-org>/hf-sandbox.git
```

Requires `huggingface-cli login` (the same token is forwarded to the sandbox so it can access HF Hub).

## Limits

- Image must have `pip` and `curl` (most do; alpine doesn't have curl by default).
- Cloudflare's free `trycloudflare.com` URLs are best-effort — fine for benchmarks, not production.
