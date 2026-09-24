"""
Client for Brønnøysundregistrene's open Enhetsregisteret API.

This is the Norwegian government's own authoritative company register.
Every request is keyed by the exact organisasjonsnummer, so there is no
fuzzy name-matching step and therefore no "wrong company" risk on this
part of the pipeline: if the API returns a record for orgnr X, that
record IS company X, by construction of the register.

Free, no API key, NLOD (open) license. Docs:
https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html
Spec: https://raw.githubusercontent.com/brreg/openAPI/master/specs/enhetsregisteret.json
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

BASE_URL = "https://data.brreg.no/enhetsregisteret/api"
USER_AGENT = "signalpost-agent/1.0 (+https://github.com/YOUR_USERNAME/signalpost-agent)"

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


class NotFound(Exception):
    """No unit (main or sub-unit) exists for this orgnr."""


class Gone(Exception):
    """Unit existed but has been removed from the register (HTTP 410)."""


class BrregError(Exception):
    """Any other non-2xx response from the API."""


@dataclass
class FetchResult:
    """A single fact-source: what we got, from where, and when."""
    data: dict[str, Any]
    source_url: str
    fetched_at: str  # ISO-8601 UTC timestamp
    unit_type: str  # "hovedenhet" (main unit) or "underenhet" (sub-unit)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
)
def _get(url: str, params: Optional[dict] = None, timeout: float = 10.0) -> requests.Response:
    resp = _session.get(url, params=params, timeout=timeout)
    if resp.status_code >= 500:
        resp.raise_for_status()  # let tenacity retry on server errors
    return resp


def fetch_unit(orgnr: str) -> FetchResult:
    """
    Fetch a company record by orgnr. Tries the main-unit (hovedenhet)
    endpoint first, then falls back to the sub-unit (underenhet) endpoint,
    since the two are disjoint (a 404 on one does not mean the number
    doesn't exist -- it might be the other kind of unit).
    """
    main_url = f"{BASE_URL}/enheter/{orgnr}"
    resp = _get(main_url)
    if resp.status_code == 200:
        return FetchResult(resp.json(), main_url, _now_iso(), "hovedenhet")
    if resp.status_code == 410:
        raise Gone(f"{orgnr} was registered but has since been removed (410)")
    if resp.status_code not in (404,):
        raise BrregError(f"unexpected status {resp.status_code} from {main_url}: {resp.text[:300]}")

    sub_url = f"{BASE_URL}/underenheter/{orgnr}"
    resp = _get(sub_url)
    if resp.status_code == 200:
        return FetchResult(resp.json(), sub_url, _now_iso(), "underenhet")
    if resp.status_code == 410:
        raise Gone(f"{orgnr} was registered but has since been removed (410)")
    if resp.status_code == 404:
        raise NotFound(f"no hovedenhet or underenhet found for orgnr {orgnr}")
    raise BrregError(f"unexpected status {resp.status_code} from {sub_url}: {resp.text[:300]}")


def fetch_updates(orgnr: str, since_iso: str) -> FetchResult:
    """
    Check the register's own change feed for this orgnr since a given
    timestamp. This is the backbone of "update correctly": rather than
    guessing whether something changed, we ask the register directly.
    `since_iso` must be like '2026-09-20T00:00:00.000Z'.
    """
    url = f"{BASE_URL}/oppdateringer/enheter"
    params = {"organisasjonsnummer": orgnr, "dato": since_iso}
    resp = _get(url, params=params)
    if resp.status_code != 200:
        raise BrregError(f"unexpected status {resp.status_code} from {url}: {resp.text[:300]}")
    return FetchResult(resp.json(), f"{url}?organisasjonsnummer={orgnr}&dato={since_iso}", _now_iso(), "oppdatering")


def sample_orgnrs(count: int = 1000, organisasjonsform: Optional[str] = None) -> list[str]:
    """
    Pull `count` REAL, currently-registered organisasjonsnummer straight
    from the register's list endpoint. Used to build a submission sample
    of >=1000 profiles without ever inventing a number. Paginates because
    a single page is capped at size=1000 by the API's own max-depth rule
    (page*size <= 10000).
    """
    out: list[str] = []
    page = 0
    size = min(count, 1000)
    while len(out) < count:
        params: dict[str, Any] = {"page": page, "size": size}
        if organisasjonsform:
            params["organisasjonsform"] = organisasjonsform
        resp = _get(f"{BASE_URL}/enheter", params=params)
        if resp.status_code != 200:
            raise BrregError(f"sample_orgnrs failed: {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        units = payload.get("_embedded", {}).get("enheter", [])
        if not units:
            break
        out.extend(u["organisasjonsnummer"] for u in units)
        page += 1
        if page * size > 10000:
            break  # API's documented max depth
    return out[:count]
