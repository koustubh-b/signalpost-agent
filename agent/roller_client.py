"""Best-effort roles (roller) client for Brønnøysundregistrene.

Same evidence rules as everything else: any failure -> None, never guess.
Only hovedenhet has roles; underenheter return 404 -> None, which is fine.
"""

from __future__ import annotations

from typing import Any, Optional

from .http_util import get_json

BASE_URL = "https://data.brreg.no/enhetsregisteret/api/enheter"


def fetch_roles(orgnr: str, timeout: float = 8.0) -> Optional[list[dict[str, Any]]]:
    """Current, non-resigned roles only. Returns None if unavailable."""
    url = f"{BASE_URL}/{orgnr}/roller"
    data = get_json(url, timeout=timeout)
    if data is None:
        return None

    roles: list[dict[str, Any]] = []
    for gruppe in data.get("rollegrupper", []) or []:
        gruppe_navn = (gruppe.get("type") or {}).get("beskrivelse", "")
        for rolle in gruppe.get("roller", []) or []:
            if rolle.get("fratraadt"):
                continue  # resigned roles are noise, not facts
            person = rolle.get("person") or {}
            navn = person.get("navn") or {}
            enhet = rolle.get("enhet") or {}
            name = " ".join(
                p for p in [navn.get("fornavn", ""), navn.get("etternavn", "")] if p
            ).strip() or enhet.get("organisasjonsnummer") or None
            if not name:
                continue
            roles.append({
                "title": (rolle.get("type") or {}).get("beskrivelse", ""),
                "group": gruppe_navn,
                "name": name,
            })
    return roles if roles else None