from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

from .http_util import get_json


BASE_URL = "https://data.brreg.no/regnskapsregisteret/regnskap"


@dataclass
class RegnskapResult:
    latest: dict[str, Any]
    source_url: str
    fetched_at: str
    period_from: Optional[str]
    period_to: Optional[str]


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def fetch_latest_accounts(
    orgnr: str,
    timeout: float = 8.0,
) -> Optional[RegnskapResult]:
    """
    Fetch the latest accounting record.

    The organisation number is always used as a URL path segment:
        /regnskap/{orgnr}
    """

    url = f"{BASE_URL}/{orgnr}"

    try:
        records = get_json(url, timeout=timeout)
    except Exception:
        return None

    if not isinstance(records, list) or not records:
        return None

    valid_records = [
        record
        for record in records
        if isinstance(record, dict)
    ]

    if not valid_records:
        return None

    def period_end(record: dict[str, Any]) -> str:
        period = record.get("regnskapsperiode")

        if not isinstance(period, dict):
            return ""

        return str(period.get("tilDato") or "")

    latest = max(valid_records, key=period_end)

    period = latest.get("regnskapsperiode")

    if not isinstance(period, dict):
        period = {}

    return RegnskapResult(
        latest=latest,
        source_url=url,
        fetched_at=_now_iso(),
        period_from=period.get("fraDato"),
        period_to=period.get("tilDato"),
    )


def _value_at(
    data: Any,
    path: tuple[str, ...],
) -> Any:
    """
    Safely read an exact nested path from a JSON dictionary.
    """

    current = data

    for key in path:
        if not isinstance(current, dict):
            return None

        if key not in current:
            return None

        current = current[key]

    return current


def _first_value(
    data: dict[str, Any],
    paths: list[tuple[str, ...]],
) -> Any:
    """
    Return the first value that actually exists in the source.
    """

    for path in paths:
        value = _value_at(data, path)

        if value is not None:
            return value

    return None


def extract_headline_figures(
    result: Optional[RegnskapResult],
) -> dict[str, Any]:
    """
    Extract financial headline figures.

    Values are copied directly from the source response.

    Missing values are omitted.

    No values are calculated or inferred.
    """

    if result is None:
        return {}

    rec = result.latest

    if not isinstance(rec, dict):
        return {}

    figures: dict[str, Any] = {}

    # =========================================================
    # CURRENCY
    # =========================================================

    value = _first_value(
        rec,
        [
            ("valuta",),
            ("currency",),
        ],
    )

    if value is not None:
        figures["currency"] = value

    # =========================================================
    # REVENUE
    # =========================================================

    value = _first_value(
        rec,
        [
            (
                "resultatregnskapResultat",
                "driftsresultat",
                "driftsinntekter",
                "sumDriftsinntekter",
            ),
            ("sumDriftsinntekter",),
            ("revenue",),
        ],
    )

    if value is not None:
        figures["revenue"] = value

    # =========================================================
    # OPERATING EXPENSES
    # =========================================================

    value = _first_value(
        rec,
        [
            (
                "resultatregnskapResultat",
                "driftsresultat",
                "driftskostnader",
                "sumDriftskostnader",
            ),
            ("sumDriftskostnader",),
            ("operating_expenses",),
        ],
    )

    if value is not None:
        figures["operating_expenses"] = value

    # =========================================================
    # OPERATING RESULT
    # =========================================================

    value = _first_value(
        rec,
        [
            (
                "resultatregnskapResultat",
                "driftsresultat",
                "driftsresultat",
            ),
            ("driftsresultat",),
            ("operating_result",),
        ],
    )

    if value is not None:
        figures["operating_result"] = value

    # =========================================================
    # NET RESULT
    # =========================================================

    value = _first_value(
        rec,
        [
            (
                "resultatregnskapResultat",
                "aarsresultat",
            ),
            (
                "resultatregnskapResultat",
                "årsresultat",
            ),
            ("aarsresultat",),
            ("årsresultat",),
            ("net_result",),
        ],
    )

    if value is not None:
        figures["net_result"] = value

    # =========================================================
    # EQUITY
    # =========================================================

    value = _first_value(
        rec,
        [
            (
                "egenkapitalGjeld",
                "egenkapital",
                "sumEgenkapital",
            ),
            ("sumEgenkapital",),
            ("equity",),
        ],
    )

    if value is not None:
        figures["equity"] = value

    # =========================================================
    # LIABILITIES
    # =========================================================

    value = _first_value(
        rec,
        [
            (
                "egenkapitalGjeld",
                "gjeld",
                "sumGjeld",
            ),
            ("sumGjeld",),
            ("liabilities",),
        ],
    )

    if value is not None:
        figures["liabilities"] = value

    # =========================================================
    # ASSETS
    #
    # EXACT FIXTURE STRUCTURE:
    #
    # "eiendeler": {
    #     "sumEiendeler": 1200000000000
    # }
    #
    # Therefore this is read directly from:
    #
    # rec["eiendeler"]["sumEiendeler"]
    # =========================================================

    assets = None

    eiendeler = rec.get("eiendeler")

    if isinstance(eiendeler, dict):
        assets = eiendeler.get("sumEiendeler")

    if assets is None:
        assets = rec.get("sumEiendeler")

    if assets is None:
        assets = rec.get("assets")

    if assets is not None:
        figures["assets"] = assets

    # =========================================================
    # WAGE COSTS
    #
    # EXACT FIXTURE STRUCTURE:
    #
    # "loennOpplysninger": {
    #     "loennskostnader": 50000000000
    # }
    # =========================================================

    wage_costs = None

    loenn = rec.get("loennOpplysninger")

    if isinstance(loenn, dict):
        wage_costs = loenn.get("loennskostnader")

    if wage_costs is None:
        wage_costs = rec.get("loennskostnader")

    if wage_costs is None:
        loenn_info = rec.get("lønnOpplysninger")

        if isinstance(loenn_info, dict):
            wage_costs = loenn_info.get("lønnskostnader")

    if wage_costs is None:
        wage_costs = rec.get("wage_costs")

    if wage_costs is not None:
        figures["wage_costs"] = wage_costs

    # =========================================================
    # IMPORTANT
    #
    # Do not add missing values as zero.
    # Do not calculate assets from equity + liabilities.
    # Do not calculate any other financial value.
    # =========================================================

    return figures