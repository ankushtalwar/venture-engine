"""Command line: `paindisc --help`."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Optional

import typer

from .config import load_settings
from .pipeline import Pipeline

app = typer.Typer(help="Pain discovery agent: verified evidence of recurring, underserved business problems.",
                  no_args_is_help=True, add_completion=False)

DEFAULT_SOURCES = ["hn", "github"]
SourceOpt = typer.Option(DEFAULT_SOURCES, "--source", "-s",
                         help="hn, github, brave, greenhouse, lever, pricing, wayback (repeat the flag)")


def _pipeline(db: Optional[Path] = None, mock: bool = False) -> Pipeline:
    settings = load_settings(db_path=db)
    if mock:
        settings.llm_mock = True
    if settings.llm_mock:
        typer.secho("LLM mock mode (no ANTHROPIC_API_KEY or LLM_MOCK=true): heuristic triage/extraction.",
                    fg=typer.colors.YELLOW)
    return Pipeline(settings)


@app.command()
def sources():
    """Show the source registry: what may be called, and why not."""
    settings = load_settings()
    from .registry import Registry
    for row in Registry.load(settings.config_dir).summary():
        mark = (typer.style(f"{'allowed':<9}", fg=typer.colors.GREEN) if row["allowed"]
                else typer.style(f"{'blocked':<9}", fg=typer.colors.RED))
        typer.echo(f"{row['source']:<18} {mark} {row['route']:<13} {row['domains']}")
        if row["reason"]:
            typer.echo(f"{'':<18} {row['reason']}")


@app.command()
def collect(source: list[str] = SourceOpt, budget: int = typer.Option(20, help="queries per query-driven source"),
            limit: int = typer.Option(50, help="results per query"), seed: Optional[int] = None):
    """Collect documents through registered routes only."""
    _pipeline().collect(source, budget=budget, limit=limit, seed=seed)


@app.command()
def triage(batch: bool = typer.Option(False, help="use the Message Batches API (50% cheaper, async)"),
           limit: Optional[int] = None):
    """Screen new documents: is this a recurring business pain?"""
    _pipeline().triage(batch=batch, limit=limit)


@app.command()
def extract(batch: bool = False, limit: Optional[int] = None):
    """Extract the 10-field evidence contract and verify every quote in code."""
    _pipeline().extract(batch=batch, limit=limit)


@app.command()
def jobs(batch: bool = False, limit: Optional[int] = None):
    """Split job posts into verified, repeatable tasks (with posted pay where stated)."""
    _pipeline().jobs(batch=batch, limit=limit)


@app.command()
def prices(batch: bool = False):
    """Extract price tiers from pricing-page snapshots."""
    _pipeline().prices(batch=batch)


@app.command()
def cluster():
    """De-duplicate and group signals into problems, then score them."""
    _pipeline().cluster()


@app.command()
def synthesize(top: int = 20, batch: bool = False):
    """Write pain-card summaries and run the skeptic pass for the top clusters."""
    _pipeline().synthesize(top=top, batch=batch)


@app.command()
def report(top: int = 20):
    """Write the Pain Report (Markdown + HTML) to reports/."""
    _pipeline().report(top=top)


@app.command()
def run(source: list[str] = SourceOpt, budget: int = 20, limit: int = 50, top: int = 20, batch: bool = False,
        seed: Optional[int] = None):
    """Collect -> triage -> extract -> jobs -> prices -> cluster -> synthesize -> report."""
    _pipeline().run(source, budget=budget, limit=limit, top=top, batch=batch, seed=seed)


@app.command()
def deepdive(top: int = 10, source: list[str] = SourceOpt):
    """Corroborate (or contradict) the top clusters with targeted searches, then re-analyse."""
    _pipeline().deepdive(top=top, sources=tuple(source))


@app.command()
def demo(fixture: Path = typer.Option(Path("tests/fixtures/demo_docs.jsonl"), help="synthetic JSONL documents"),
         db: Path = typer.Option(Path("data/demo.sqlite"))):
    """Run the whole pipeline offline on synthetic fixtures (mock LLM). Writes reports/demo-pain-report.*"""
    if db.exists():
        db.unlink()
    p = _pipeline(db=db, mock=True)
    new, dupes = p.load_fixture(fixture)
    typer.echo(f"loaded {new} synthetic documents ({dupes} duplicates)")
    p.triage()
    p.extract()
    p.jobs()
    p.prices()
    p.cluster(threshold=0.75)  # mock canonicals are raw sentences, so group more loosely than real runs
    p.synthesize()
    p.report(demo=True, stem="demo-pain-report")


@app.command()
def label(top: int = 20):
    """Accept or reject the top pain cards. Labels feed tuning and the ranker."""
    p = _pipeline()
    done = p.store.labels("cluster")
    for c in p.store.clusters()[:top]:
        if c.id in done:
            continue
        title = c.synthesis.title if c.synthesis else c.canonical
        typer.echo(f"\n[{c.confidence} {c.score:.2f}] {title}\n  {c.n_signals} signals, {c.n_authors} authors, "
                   f"{', '.join(c.source_types)}")
        answer = typer.prompt("  accept / reject / skip / quit", default="s")[:1].lower()
        if answer == "q":
            break
        if answer in ("a", "r"):
            p.store.set_label("cluster", c.id, "accept" if answer == "a" else "reject")


@app.command("export-review")
def export_review(out: Path = Path("reports/review.csv")):
    """Export verified signals to CSV for spreadsheet review (fill the 'label' column)."""
    p = _pipeline()
    cluster_of = {sid: c.id for c in p.store.clusters() for sid in c.signal_ids}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["signal_id", "cluster_id", "source", "url", "date", "problem_quote", "workaround",
                         "frequency", "spend", "label"])
        for s, d in p.store.signals_with_docs():
            writer.writerow([s.id, cluster_of.get(s.id, ""), d.source, d.url,
                             d.published_at.date().isoformat() if d.published_at else "", s.problem.quote,
                             s.workaround.quote, s.occurrence_freq.value,
                             "; ".join(f"{x.type} {x.amount}".strip() for x in s.spend_evidence), ""])
    typer.echo(f"wrote {out}")


@app.command("import-labels")
def import_labels(path: Path):
    """Import the 'label' column from an export-review CSV (valid / invalid / duplicate)."""
    p = _pipeline()
    n = 0
    with path.open() as fh:
        for row in csv.DictReader(fh):
            if row.get("label"):
                p.store.set_label("signal", row["signal_id"], row["label"].strip().lower())
                n += 1
    typer.echo(f"imported {n} labels")


@app.command("eval")
def evaluate_cmd(gold: Path):
    """Score triage and verification against a hand-labelled gold set (JSONL)."""
    from .evaluate import evaluate
    typer.echo(json.dumps(evaluate(_pipeline().store, gold), indent=2, default=str))


@app.command("boards-discover")
def boards_discover(role: str, ats: str = typer.Option("both", help="greenhouse, lever or both")):
    """Find Greenhouse/Lever boards hiring a role (needs BRAVE_API_KEY). Add the tokens to config/job_boards.yaml."""
    from .collectors import BraveCollector
    p = _pipeline()
    brave = BraveCollector(p.client, p.settings)
    patterns = {
        "greenhouse": (f'site:boards.greenhouse.io "{role}"', r"(?:job-)?boards\.greenhouse\.io/([A-Za-z0-9_-]+)"),
        "lever": (f'site:jobs.lever.co "{role}"', r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    }
    for name, (query, pattern) in patterns.items():
        if ats not in ("both", name):
            continue
        tokens = sorted({m.group(1) for r in brave.search(query) for m in [re.search(pattern, r.get("url", ""))] if m}
                        - {"embed", "jobs"})
        typer.echo(f"{name}: {', '.join(tokens) or 'none found'}")


@app.command()
def stats():
    """Documents, triage, signals and drops per source; best and worst query arms."""
    p = _pipeline()
    for row in p.store.source_stats():
        typer.echo(f"{row['source']:<14} docs {row['docs']:>5}  dupes {row['duplicates']:>4}  pain "
                   f"{row['pain']:>4}/{row['triaged']:<5} signals {row['signals']:>4}  dropped {row['dropped']:>3}")
    arms = sorted(p.store.arm_stats().values(), key=lambda a: (a["verified"], a["docs"]), reverse=True)
    if arms:
        typer.echo("\nTop query arms (verified docs / docs / runs):")
        for a in arms[:10]:
            typer.echo(f"  {a['verified']:>3}/{a['docs']:<4} runs {a['runs']:<3} [{a['source']}] {a['query']}")


if __name__ == "__main__":
    app()
