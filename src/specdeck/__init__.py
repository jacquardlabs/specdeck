"""specdeck — card-based eval runner for LLM systems."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("specdeck")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
