"""`python -m paindisc.api [--db PATH] [--host H] [--port P] [--reload] [--mock]`"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import load_settings
from .app import run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m paindisc.api", description="Serve the review UI JSON API.")
    parser.add_argument("--db", type=Path, help="SQLite file (overrides DATABASE_URL)")
    parser.add_argument("--home", type=Path, help="project home (config/, prompts/, reports/); default cwd")
    parser.add_argument("--host", help="default PAINDISC_API_HOST or 127.0.0.1")
    parser.add_argument("--port", type=int, help="default PAINDISC_API_PORT or 8000")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    parser.add_argument("--mock", action="store_true", help="force the mock LLM even if a key is set")
    args = parser.parse_args(argv)

    settings = load_settings(home=args.home, db_path=args.db)
    if args.db is not None:
        settings.database_url = None
    if args.mock:
        settings.llm_mock = True
    run(settings, args.host or settings.api_host, args.port or settings.api_port, reload=args.reload)


if __name__ == "__main__":
    main()
