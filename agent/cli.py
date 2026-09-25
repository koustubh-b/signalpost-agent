"""
Command-line entrypoint.

Usage (see README for full examples):
    python -m agent.cli lookup 923609016
    python -m agent.cli batch orgnrs.txt --out profiles.jsonl
    python -m agent.cli sample --count 1000 --out sample_orgnrs.txt
"""

from __future__ import annotations

import json
import sys
import time
import traceback

import click

from .cache import ProfileStore
from .orgnr import InvalidOrgNumber
from .profile_builder import build_profile
from . import brreg_client


@click.group()
def cli():
    """Signalpost agent: look up Norwegian company facts with evidence."""


@cli.command()
@click.argument("orgnr")
@click.option("--out", type=click.Path(), default=None, help="Write JSON to this file instead of stdout.")
@click.option("--no-financials", is_flag=True, help="Skip the best-effort accounts lookup.")
@click.option("--db", default="signalpost_cache.db", help="Path to the local change-tracking cache.")
def lookup(orgnr: str, out: str | None, no_financials: bool, db: str):
    """Look up a single company by organisasjonsnummer."""
    store = ProfileStore(db)
    try:
        profile = build_profile(orgnr, store=store, include_financials=not no_financials)
    except InvalidOrgNumber as exc:
        click.echo(json.dumps({"organisasjonsnummer": orgnr, "error": f"invalid orgnr: {exc}"}), err=True)
        sys.exit(2)
    except brreg_client.NotFound as exc:
        click.echo(json.dumps({"organisasjonsnummer": orgnr, "error": str(exc)}), err=True)
        sys.exit(1)
    except brreg_client.Gone as exc:
        click.echo(json.dumps({"organisasjonsnummer": orgnr, "error": str(exc), "status": "deregistered"}), err=True)
        sys.exit(1)
    finally:
        store.close()

    text = json.dumps(profile, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        click.echo(f"Wrote profile for {orgnr} to {out}")
    else:
        click.echo(text)


@cli.command()
@click.argument("infile", type=click.Path(exists=True))
@click.option("--out", type=click.Path(), default="profiles.jsonl", help="Output JSONL file, one profile per line.")
@click.option("--no-financials", is_flag=True, help="Skip the best-effort accounts lookup (faster, fewer requests).")
@click.option("--db", default="signalpost_cache.db", help="Path to the local change-tracking cache.")
@click.option("--sleep", default=0.05, help="Seconds to sleep between requests (politeness / rate-limit safety).")
def batch(infile: str, out: str, no_financials: bool, db: str, sleep: float):
    """
    Look up every organisasjonsnummer listed (one per line) in INFILE and
    write newline-delimited JSON profiles to --out. Skips and logs bad
    numbers or not-found companies rather than aborting the whole run.
    """
    with open(infile, encoding="utf-8") as f:
        orgnrs = [line.strip() for line in f if line.strip()]

    store = ProfileStore(db)
    ok, failed = 0, 0
    with open(out, "w", encoding="utf-8") as f_out:
        for i, orgnr in enumerate(orgnrs, 1):
            try:
                profile = build_profile(orgnr, store=store, include_financials=not no_financials)
                f_out.write(json.dumps(profile, ensure_ascii=False) + "\n")
                ok += 1
            except Exception as exc:
                failed += 1
                f_out.write(json.dumps({"organisasjonsnummer": orgnr, "error": str(exc)}) + "\n")
                click.echo(f"[{i}/{len(orgnrs)}] FAILED {orgnr}: {exc}", err=True)
            if i % 50 == 0:
                click.echo(f"[{i}/{len(orgnrs)}] processed ({ok} ok, {failed} failed)...")
            time.sleep(sleep)
    store.close()
    click.echo(f"Done. {ok} succeeded, {failed} failed. Written to {out}")


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