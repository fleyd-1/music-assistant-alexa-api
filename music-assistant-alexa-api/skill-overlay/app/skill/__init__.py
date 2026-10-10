"""Extend the pinned skill package with this fork's voice-search handler."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
