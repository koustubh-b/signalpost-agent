"""Submission gate: every fact in every profile must carry evidence.
Run: py -3 scripts/validate_profiles.py profiles.jsonl
Exits 0 if clean, 1 if any problem -- run it before every commit."""

from __future__ import annotations

import json
import sys

REQUIRED_KEYS = ("value", "source_url", "retrieved_at", "explanation")


def main(path: str) -> None:
    total = 0
    problems = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            total += 1
            if not p.get("organisasjonsnummer") or not p.get("as_of"):
                problems += 1
                print(f"line {total}: missing organisasjonsnummer/as_of")
            for fname, fact in (p.get("facts") or {}).items():
                if not isinstance(fact, dict):
                    problems += 1
                    print(f"{p.get('organisasjonsnummer')} fact '{fname}': not a dict")
                    continue
                missing = [k for k in REQUIRED_KEYS if k not in fact]
                if missing:
                    problems += 1
                    print(f"{p.get('organisasjonsnummer')} fact '{fname}': missing {missing}")
    print(f"checked {total} profiles -> {problems} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "profiles.jsonl")