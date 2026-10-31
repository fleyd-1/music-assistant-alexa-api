"""WSGI entry point: the upstream web app, with the extension installed first."""

import os

# The ask-sdk builder created when the skill module is imported needs a region
# (upstream's app.py sets the same default before importing it).
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import fork_ext  # noqa: E402

fork_ext.install()

from app import app  # noqa: E402,F401  (upstream app builds its skill on import)
