from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class Settings:
    home: Path
    db_path: Path
    snapshot_dir: Path
    report_dir: Path
    config_dir: Path
    prompts_dir: Path
    anthropic_api_key: str | None
    github_token: str | None
    brave_api_key: str | None
    llm_mock: bool
    model_triage: str
    model_extract: str
    model_synth: str
    user_agent: str

    def load_yaml(self, name: str) -> dict:
        path = self.config_dir / name
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text()) or {}

    def prompt(self, name: str) -> str:
        return (self.prompts_dir / f"{name}.md").read_text()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(home: Path | str | None = None, db_path: Path | str | None = None) -> Settings:
    home = Path(home or os.environ.get("PAINDISC_HOME") or Path.cwd()).resolve()
    load_dotenv(home / ".env")
    key = os.environ.get("ANTHROPIC_API_KEY") or None
    return Settings(
        home=home,
        db_path=Path(db_path) if db_path else home / "data" / "paindisc.sqlite",
        snapshot_dir=home / "data" / "snapshots",
        report_dir=home / "reports",
        config_dir=home / "config",
        prompts_dir=home / "prompts",
        anthropic_api_key=key,
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        brave_api_key=os.environ.get("BRAVE_API_KEY") or None,
        llm_mock=_truthy(os.environ.get("LLM_MOCK")) or key is None,
        model_triage=os.environ.get("PAINDISC_MODEL_TRIAGE", "claude-haiku-5-5"),
        model_extract=os.environ.get("PAINDISC_MODEL_EXTRACT", "claude-sonnet-5-5"),
        model_synth=os.environ.get("PAINDISC_MODEL_SYNTH", "claude-opus-5-5"),
        user_agent=os.environ.get(
            "PAINDISC_USER_AGENT", "paindisc/0.1 (research crawler; set PAINDISC_USER_AGENT with a contact)"
        ),
    )
