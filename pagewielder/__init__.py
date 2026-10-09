"""A tool for manipulating PDFs."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("pagewielder")
except importlib.metadata.PackageNotFoundError:
    # Run from a checkout without being installed, as in the Nix dev shell.
    __version__ = "unknown"
