"""Validate a Signalpost batch output against its input.

Usage:
  python scripts/validate_smoke.py input_orgnrs.txt profiles.jsonl

Checks:
- exactly one result per non-empty input line
- valid JSON on every output line
- matching organisation number per line
- required envelope fields and allowed states
- no missing result rows
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STATES = {
    "available",
    "not_available",
    "blocked",
    "not_applicable",
    "ambiguous",
    "failed",
}

REQUIRED = {
    "organisasjonsnummer",
    "as_of",
    "unit_type",
    "state",
    "facts",
    "changes_since_last_check",
    "changes_explanation",
    "errors",
}


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: python scripts/validate_smoke.py "
            "INPUT_ORGNRS OUTPUT_JSONL"
        )
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    inputs = [
        x.strip()
        for x in input_path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]

    outputs_raw = output_path.read_text(
        encoding="utf-8"
    ).splitlines()

    if len(outputs_raw) != len(inputs):
        print(
            f"FAIL: inputs={len(inputs)} "
            f"outputs={len(outputs_raw)}"
        )
        return 1

    seen = set()

    for index, (orgnr, raw) in enumerate(
        zip(inputs, outputs_raw), 1
    ):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(
                f"FAIL: line {index} "
                f"invalid JSON: {exc}"
            )
            return 1

        missing = REQUIRED - obj.keys()

        if missing:
            print(
                f"FAIL: line {index} "
                f"missing fields: {sorted(missing)}"
            )
            return 1

        if str(obj["organisasjonsnummer"]) != orgnr:
            print(
                f"FAIL: line {index} orgnr mismatch: "
                f"input={orgnr} "
                f"output={obj['organisasjonsnummer']}"
            )
            return 1

        if obj["state"] not in STATES:
            print(
                f"FAIL: line {index} "
                f"invalid state: {obj['state']}"
            )
            return 1

        if not isinstance(obj["facts"], dict):
            print(
                f"FAIL: line {index} "
                f"facts is not an object"
            )
            return 1

        seen.add(orgnr)

    print(
        f"PASS: {len(inputs)} inputs -> "
        f"{len(outputs_raw)} terminal envelopes"
    )

    print(
        f"Unique organisation numbers: "
        f"{len(seen)}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())