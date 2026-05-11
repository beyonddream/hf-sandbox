from importlib.metadata import PackageNotFoundError, version

from huggingface_sandbox.client import Sandbox

try:
  __version__ = version('huggingface-sandbox')
except:
  __version__ = '0.0.0'

__all__ = ['Sandbox', '__version__']