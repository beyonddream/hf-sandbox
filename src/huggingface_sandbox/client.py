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

