from importlib.metadata import PackageNotFoundError, version

from hf_sandbox.client import Sandbox

try:
    __version__ = version("hf-sandbox")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["Sandbox", "__version__"]
