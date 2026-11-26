"""Review UI JSON API (FastAPI). Contract: docs/api.md. Entry points: `paindisc serve`, `python -m paindisc.api`."""

from .app import create_app, run

__all__ = ["create_app", "run"]
