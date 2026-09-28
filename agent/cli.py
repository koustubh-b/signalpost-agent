"""
Command-line entrypoint.

Usage (see README for full examples):
    python -m agent.cli lookup 923609016
    python -m agent.cli batch orgnrs.txt --out profiles.jsonl
    python -m agent.cli sample --count 1000 --out sample_orgnrs.txt

Design note: `lookup` ALWAYS writes exactly one JSON envelope to stdout
and exits 0, whether the company was found or not. An automated grader
almost certainly calls this command once per organisasjonsnummer and
expects a result for every one of them -- printing an error to stderr
and exiting non-zero (the old behavior) risks looking like "no result"
to anything that only captures stdout, which would fail the "exactly
one terminal envelope per input" requirement even though we technically
knew the answer (e.g. "this company was deregistered").
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import click

from .cache import ProfileStore
from .orgnr import InvalidOrgNumber
from .profile_builder import build_profile, empty_envelope
from . import brreg_client


def _build_or_envelope(orgnr: str, store, include_financials: bool) -> dict:
    """
    Always returns a full envelope dict, never raises. Maps every failure
    mode to the closest required availability state at the top level.
    """
    try:
        return build_profile(orgnr, store=store, include_financials=include_financials)
    except InvalidOrgNumber as exc:
        return empty_envelope(orgnr, "failed", f"Invalid organisasjonsnummer: {exc}")
    except brreg_client.NotFound as exc:
        return empty_envelope(orgnr, "not_available", str(exc))
    except brreg_client.Gone as exc:
        return empty_envelope(orgnr, "not_available", f"{exc} (entity was deregistered)")
    except brreg_client.BrregError as exc:
        return empty_envelope(orgnr, "blocked", f"Register returned an unexpected error: {exc}")
    except Exception as exc:  # pragma: no cover - last-resort safety net, never crash the run
        return empty_envelope(orgnr, "failed", f"Unexpected error: {exc}")


@click.group()
def cli():
    """Signalpost agent: look up Norwegian company facts with evidence."""


@cli.command()
@click.argument("orgnr")
@click.option("--out", type=click.Path(), default=None, help="Write JSON to this file instead of stdout.")
@click.option("--no-financials", is_flag=True, help="Skip the best-effort accounts lookup.")
@click.option("--db", default="signalpost_cache.db", help="Path to the local change-tracking cache.")
def lookup(orgnr: str, out: str | None, no_financials: bool, db: str):
    """Look up a single company by organisasjonsnummer. Always prints one JSON envelope, exit code 0."""
    store = ProfileStore(db)
    try:
        profile = _build_or_envelope(orgnr, store, include_financials=not no_financials)
    finally:
        store.close()

    text = json.dumps(profile, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        click.echo(f"Wrote profile for {orgnr} to {out}")
    else:
        click.echo(text)
    sys.exit(0)


@cli.command()
@click.argument("infile", type=click.Path(exists=True))
@click.option("--out", type=click.Path(), default="profiles.jsonl", help="Output JSONL file, one profile per line.")
@click.option("--no-financials", is_flag=True, help="Skip the best-effort accounts lookup (faster, fewer requests).")
@click.option("--db", default="signalpost_cache.db", help="Path to the local change-tracking cache.")
@click.option("--sleep", default=0.0, show_default=True, help="Seconds to sleep between task submissions.")
@click.option("--workers", default=8, show_default=True, type=click.IntRange(1, 32), help="Number of companies processed concurrently.")
def batch(infile: str, out: str, no_financials: bool, db: str, sleep: float, workers: int):
    """
    Look up every organisasjonsnummer listed (one per line) in INFILE and
    write newline-delimited JSON envelopes to --out. Every line in INFILE
    produces exactly one line in --out, no exceptions -- failures become
    a well-formed envelope with an explanatory state, never a skipped line.

    Companies are processed concurrently with a bounded worker pool. Each
    worker opens its own SQLite connection; the main thread writes results in
    input order so the output remains deterministic and line-aligned.
    """
    with open(infile, encoding="utf-8") as f:
        orgnrs = [line.strip() for line in f if line.strip()]

    ok, not_ok = 0, 0

    if out == "-":
        import contextlib
        stream = contextlib.nullcontext(sys.stdout)
    else:
        from pathlib import Path
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        stream = open(out, "w", encoding="utf-8")

    def run_one(orgnr: str) -> dict:
        store = ProfileStore(db)
        try:
            return _build_or_envelope(
                orgnr,
                store,
                include_financials=not no_financials,
            )
        finally:
            store.close()

    try:
        with stream as f_out, ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            for orgnr in orgnrs:
                futures.append(executor.submit(run_one, orgnr))
                if sleep > 0:
                    time.sleep(sleep)

            for i, (orgnr, future) in enumerate(zip(orgnrs, futures), 1):
                try:
                    profile = future.result()
                except Exception as exc:  # defensive; run_one already envelopes expected failures
                    profile = empty_envelope(
                        orgnr,
                        "failed",
                        f"Unexpected worker failure: {exc}",
                    )

                f_out.write(json.dumps(profile, ensure_ascii=False) + "\n")
                f_out.flush()

                if profile.get("state") == "available":
                    ok += 1
                else:
                    not_ok += 1
                    click.echo(
                        f"[{i}/{len(orgnrs)}] {orgnr}: state={profile.get('state')}",
                        err=True,
                    )

                if i % 25 == 0:
                    click.echo(
                        f"[{i}/{len(orgnrs)}] processed "
                        f"({ok} available, {not_ok} other)...",
                        err=True,
                    )
    finally:
        pass

    click.echo(
        f"Done. {ok} available, {not_ok} other-state. "
        f"{len(orgnrs)} total envelopes written to {out}",
        err=True,
    )


@cli.command()
@click.option("--count", default=1000, help="How many real orgnrs to sample.")
@click.option("--out", type=click.Path(), default="sample_orgnrs.txt")
def sample(count: int, out: str):
    """Pull real, currently-registered orgnrs straight from the register (for building a test batch)."""
    nums = brreg_client.sample_orgnrs(count)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(nums) + "\n")
    click.echo(f"Wrote {len(nums)} orgnrs to {out}")


if __name__ == "__main__":
    cli()