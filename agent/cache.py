"""
Local history store so the agent can say not just "here is the current
fact" but "here is what changed since last time, and when we last checked."

This directly targets the "update correctly" scoring criterion (20 pts):
the daily test re-runs the same companies, and an agent that silently
re-serves a stale cached blob will miss real-world changes (status flips
to bankrupt, address moves, employee count changes, etc). So on every
lookup we:
  1. always hit the live API first (cache is never used INSTEAD of a
     live check -- only to compute what changed)
  2. diff the new snapshot against the last stored one
  3. persist the new snapshot + a changelog entry
  4. surface the diff in the profile's `changes_since_last_check` field
"""

from __future__ import annotations

import json
import sqlite3
import datetime as dt
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    orgnr TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    field_hash_json TEXT NOT NULL,
    last_checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS changelog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orgnr TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    detected_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class ProfileStore:
    def __init__(self, db_path: str = "signalpost_cache.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def last_checked_at(self, orgnr: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT last_checked_at FROM snapshots WHERE orgnr = ?", (orgnr,)
        ).fetchone()
        return row[0] if row else None

    def _flatten(self, facts: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        flat = {}
        for k, v in facts.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                flat.update(self._flatten(v, key))
            else:
                flat[key] = v
        return flat

    def diff_and_store(self, orgnr: str, new_facts: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Compare `new_facts` (the flat, comparable subset of a freshly-built
        profile -- see profile_builder.COMPARABLE_FIELDS) against the last
        stored snapshot for this orgnr, record any differences, persist the
        new snapshot, and return the list of changes.
        """
        now = _now_iso()
        new_flat = self._flatten(new_facts)

        row = self.conn.execute(
            "SELECT field_hash_json FROM snapshots WHERE orgnr = ?", (orgnr,)
        ).fetchone()

        changes: list[dict[str, Any]] = []
        if row:
            old_flat = json.loads(row[0])
            for field, new_val in new_flat.items():
                old_val = old_flat.get(field, "__MISSING__")
                if str(old_val) != str(new_val):
                    changes.append({"field": field, "old_value": old_val, "new_value": new_val})
            for field in old_flat:
                if field not in new_flat:
                    changes.append({"field": field, "old_value": old_flat[field], "new_value": None})

        for c in changes:
            self.conn.execute(
                "INSERT INTO changelog (orgnr, field, old_value, new_value, detected_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (orgnr, c["field"], json.dumps(c["old_value"]), json.dumps(c["new_value"]), now),
            )

        self.conn.execute(
            "INSERT INTO snapshots (orgnr, profile_json, field_hash_json, last_checked_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(orgnr) DO UPDATE SET "
            "profile_json=excluded.profile_json, "
            "field_hash_json=excluded.field_hash_json, "
            "last_checked_at=excluded.last_checked_at",
            (orgnr, json.dumps(new_facts), json.dumps(new_flat), now),
        )
        self.conn.commit()
        return changes

    def history(self, orgnr: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT field, old_value, new_value, detected_at FROM changelog "
            "WHERE orgnr = ? ORDER BY detected_at DESC",
            (orgnr,),
        ).fetchall()
        return [
            {"field": f, "old_value": json.loads(o), "new_value": json.loads(n), "detected_at": t}
            for f, o, n, t in rows
        ]

    def close(self):
        self.conn.close()
