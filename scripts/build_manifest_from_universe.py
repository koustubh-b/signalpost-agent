"""
Build sample_orgnrs.txt by sampling organisation numbers
from the supplied official company-universe file.

Usage:
    py -3 scripts/build_manifest_from_universe.py <universe_file> [count] [output]

Examples:
    py -3 scripts/build_manifest_from_universe.py universe.jsonl.gz
    py -3 scripts/build_manifest_from_universe.py universe.jsonl.gz 100
    py -3 scripts/build_manifest_from_universe.py universe.jsonl.gz 1000 sample_orgnrs.txt
"""

from __future__ import annotations

import gzip
import json
import random
import sys
from pathlib import Path


# Possible organisation-number field names.
CANDIDATE_KEYS = (
    "organisasjonsnummer",
    "orgnr",
    "org_number",
    "organisation_number",
    "organization_number",
    "id",
)


def extract_orgnr(record: dict) -> str | None:
    """Extract an organisation number from one JSON record."""

    for key in CANDIDATE_KEYS:
        value = record.get(key)

        if value is not None and str(value).strip():
            orgnr = str(value).strip()

            # Keep only plausible Norwegian organisation numbers.
            digits = "".join(
                ch for ch in orgnr
                if ch.isdigit()
            )

            if len(digits) == 9:
                return digits

    return None


def open_universe(path: str):
    """Open normal JSONL or gzipped JSONL."""

    if path.lower().endswith(".gz"):
        return gzip.open(
            path,
            "rt",
            encoding="utf-8",
        )

    return open(
        path,
        "r",
        encoding="utf-8",
    )


def load_orgnrs(universe_path: str) -> list[str]:
    """Read organisation numbers from the universe."""

    orgnrs: list[str] = []
    seen: set[str] = set()

    first_raw_line: str | None = None

    with open_universe(universe_path) as file:

        for line_number, line in enumerate(
            file,
            start=1,
        ):
            line = line.strip()

            if not line:
                continue

            if first_raw_line is None:
                first_raw_line = line

            try:
                record = json.loads(line)

            except json.JSONDecodeError:
                print(
                    f"Warning: invalid JSON at "
                    f"line {line_number}; skipping."
                )
                continue

            if not isinstance(record, dict):
                continue

            orgnr = extract_orgnr(record)

            if not orgnr:
                continue

            if orgnr in seen:
                continue

            seen.add(orgnr)
            orgnrs.append(orgnr)

    if not orgnrs:
        print(
            "\nERROR: No organisation numbers were found."
        )

        if first_raw_line:
            print(
                "\nFirst JSON record found:"
            )
            print(first_raw_line)

        print(
            "\nExpected one of these keys:"
        )

        for key in CANDIDATE_KEYS:
            print(f"  - {key}")

        raise SystemExit(1)

    return orgnrs


def write_manifest(
    orgnrs: list[str],
    output_path: str,
) -> None:
    """Write one organisation number per line."""

    output = Path(output_path)

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:

        for orgnr in orgnrs:
            file.write(orgnr + "\n")


def main(
    universe_path: str,
    count: int,
    output_path: str,
) -> None:

    if count <= 0:
        print(
            "ERROR: count must be greater than 0."
        )
        raise SystemExit(1)

    print(
        f"Reading official universe: "
        f"{universe_path}"
    )

    all_orgnrs = load_orgnrs(
        universe_path
    )

    print(
        f"Found {len(all_orgnrs):,} unique "
        f"organisation numbers."
    )

    sample_size = min(
        count,
        len(all_orgnrs),
    )

    # Fixed seed makes the smoke-test sample reproducible.
    random.seed(42)

    selected = random.sample(
        all_orgnrs,
        sample_size,
    )

    write_manifest(
        selected,
        output_path,
    )

    print(
        f"Selected {len(selected):,} "
        f"organisation numbers."
    )

    print(
        f"Manifest written to: "
        f"{output_path}"
    )


if __name__ == "__main__":

    if len(sys.argv) < 2:
        print(
            "Usage:"
        )
        print(
            "  py -3 scripts/"
            "build_manifest_from_universe.py "
            "<universe_file> [count] [output]"
        )
        raise SystemExit(2)

    universe_file = sys.argv[1]

    count = (
        int(sys.argv[2])
        if len(sys.argv) >= 3
        else 1000
    )

    output_file = (
        sys.argv[3]
        if len(sys.argv) >= 4
        else "sample_orgnrs.txt"
    )

    main(
        universe_file,
        count,
        output_file,
    )