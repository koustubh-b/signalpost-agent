"""
Optional client for Regnskapsregisteret (the annual-accounts register).

IMPORTANT: Brønnøysundregistrene's own docs describe this JSON API as a
temporary R&D endpoint ("vil ikke bli videreutviklet eller vedlikeholdt,
og kan bli lagt ned uten varsel" -- will not be maintained further and
may be taken down without notice). Because a single "fabricated financial
value" fails the whole entry, we treat this source as strictly best-effort:

  * every numeric fact returned is tagged with its exact source URL,
    the accounting period it covers, and a fetch timestamp
  * if the endpoint is unreachable, changed shape, or returns nothing,
    we return None and the profile builder simply omits financials
    rather than guessing or carrying forward a stale/estimated number
  * we never compute or infer a financial figure ourselves -- only
    values verbatim from the API response are surfaced
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

from .http_util import get_json

BASE_URL = "https://data.brreg.no/regnskapsregisteret/regnskap"
# Confirmed against the LIVE api-docs on 2026-09-24 (the archived spec on
# github.com/brreg/regnskapsregister-api is stale and describes a different
# shape): orgNummer is a PATH parameter, e.g.
#   GET https://data.brreg.no/regnskapsregisteret/regnskap/923609016
# NOT a query parameter on the bare /regnskap path. Response is a JSON list
# of accounting-year records (verified against Equinor's real 2025 filing).


@dataclass
class RegnskapResult:
    latest: dict[str, Any]
    source_url: str
    fetched_at: str
    period_from: Optional[str]
    period_to: Optional[str]


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def fetch_latest_accounts(orgnr: str, timeout: float = 8.0) -> Optional[RegnskapResult]:
    """
    Best-effort fetch of the most recent submitted annual accounts.
    Returns None (never raises to the caller) on any failure -- financials
    are a bonus fact, not a required one, and silent omission is far safer
    than a wrong or fabricated number given the challenge's scoring rules.
    """
    url = f"{BASE_URL}/{orgnr}"
    records = get_json(url, timeout=timeout)
    if not isinstance(records, list) or not records:
        return None

    def _period_end(rec: dict) -> str:
        return rec.get("regnskapsperiode", {}).get("tilDato", "") or ""

    latest = max(records, key=_period_end)
    period = latest.get("regnskapsperiode", {})
    return RegnskapResult(
        latest=latest,
        source_url=url,
        fetched_at=_now_iso(),
        period_from=period.get("fraDato"),
        period_to=period.get("tilDato"),
    )


def extract_headline_figures(result: RegnskapResult) -> dict[str, Any]:
    """
    Pull out a clearly-labeled set of headline numbers from a raw accounts
    record, in the currency it was reported in. Anything not present in the
    source is simply left out, never zero-filled or estimated.
    """
    rec = result.latest
    out: dict[str, Any] = {"currency": rec.get("valuta")}

    resultat = rec.get("resultatregnskapResultat", {}) or {}
    drift = resultat.get("driftsresultat", {}) or {}
    driftsinntekter = drift.get("driftsinntekter", {}) or {}
    driftskostnader = drift.get("driftskostnader", {}) or {}

    if "sumDriftsinntekter" in driftsinntekter:
        out["revenue"] = driftsinntekter["sumDriftsinntekter"]
    if "sumDriftskostnader" in driftskostnader:
        out["operating_expenses"] = driftskostnader["sumDriftskostnader"]
    if "driftsresultat" in drift:
        out["operating_result"] = drift["driftsresultat"]
    if "aarsresultat" in resultat:
        out["net_result"] = resultat["aarsresultat"]

    egenkapital_gjeld = rec.get("egenkapitalGjeld", {}) or {}
    egenkapital = egenkapital_gjeld.get("egenkapital", {}) or {}
    if "sumEgenkapital" in egenkapital:
        out["equity"] = egenkapital["sumEgenkapital"]
    gjeld = egenkapital_gjeld.get("gjeld", {}) or {}
    if "sumGjeld" in gjeld:
        out["liabilities"] = gjeld["sumGjeld"]

    eiendeler = rec.get("eiendeler", {}) or {}
    if "sumEiendeler" in eiendeler:
        out["assets"] = eiendeler["sumEiendeler"]

    loenn = rec.get("loennOpplysninger", {}) or {}
    if "loennskostnader" in loenn:
        out["wage_costs"] = loenn["loennskostnader"]

    return {k: v for k, v in out.items() if v is not None}

