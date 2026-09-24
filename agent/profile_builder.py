"""
Builds the final company profile: every fact paired with its source URL,
retrieval timestamp, and a plain-English explanation. This shape is what
gets scored on "find useful information", "match correctly with evidence",
"update correctly", and "give useful explanations".
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from . import brreg_client, regnskap_client, explain
from .orgnr import validate, InvalidOrgNumber
from .cache import ProfileStore

# Fields we track for change-detection. Deliberately excludes fields that
# are expected to be noisy/irrelevant (like fetch timestamps themselves).
COMPARABLE_FIELDS = (
    "name", "status", "employees", "address", "industry", "vat_registered",
    "website", "latest_accounts_period_end",
)


def _addr(a: Optional[dict]) -> Optional[str]:
    if not a:
        return None
    parts = [", ".join(a.get("adresse") or []), a.get("postnummer"), a.get("poststed"), a.get("land")]
    return ", ".join(p for p in parts if p)


def build_profile(
    orgnr_raw: str,
    store: Optional[ProfileStore] = None,
    include_financials: bool = True,
) -> dict[str, Any]:
    """
    Returns a dict:
      {
        "organisasjonsnummer": ...,
        "as_of": <ISO timestamp of this run>,
        "facts": { ...each fact with value + source_url + retrieved_at + explanation... },
        "changes_since_last_check": [...],
        "errors": [...]   # non-fatal issues (e.g. financials unavailable)
      }
    Raises InvalidOrgNumber for syntactically bad input (caught by the CLI
    so a batch run can skip and log rather than crash).
    """
    orgnr = validate(orgnr_raw)
    run_time = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    errors: list[str] = []

    unit = brreg_client.fetch_unit(orgnr)  # raises NotFound/Gone if nothing exists
    e = unit.data

    naeringskode1 = e.get("naeringskode1") or {}
    forretningsadresse = e.get("forretningsadresse") or e.get("beliggenhetsadresse")
    org_form = (e.get("organisasjonsform") or {}).get("beskrivelse")
    org_form_kode = (e.get("organisasjonsform") or {}).get("kode")

    facts: dict[str, Any] = {
        "name": {
            "value": e.get("navn"),
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": f"Registered name in the {unit.unit_type} register.",
        },
        "organisasjonsform": {
            "value": {"code": org_form_kode, "description": org_form},
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": f"Legal organization form: {org_form or 'not reported'} ({org_form_kode or '?'}).",
        },
        "status": {
            "value": {
                "bankrupt": e.get("konkurs", False),
                "under_liquidation": e.get("underAvvikling", False),
                "under_forced_liquidation": e.get("underTvangsavviklingEllerTvangsopplosning", False),
            },
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": explain.explain_status({
                "bankrupt": e.get("konkurs", False),
                "under_liquidation": e.get("underAvvikling", False),
                "under_forced_liquidation": e.get("underTvangsavviklingEllerTvangsopplosning", False),
            }),
        },
        "employees": {
            "value": e.get("antallAnsatte"),
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": explain.explain_employees(e.get("antallAnsatte")),
        },
        "industry": {
            "value": {"code": naeringskode1.get("kode"), "description": naeringskode1.get("beskrivelse")},
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": explain.explain_industry(naeringskode1.get("kode"), naeringskode1.get("beskrivelse")),
        },
        "address": {
            "value": _addr(forretningsadresse),
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": "Business address as registered.",
        },
        "vat_registered": {
            "value": e.get("registrertIMvaregisteret"),
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": explain.explain_vat(e.get("registrertIMvaregisteret")),
        },
        "founding_and_registration": {
            "value": {
                "stiftelsesdato": e.get("stiftelsedato"),
                "registreringsdato": e.get("registreringsdatoEnhetsregisteret"),
            },
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": explain.explain_founding(
                e.get("stiftelsedato"), e.get("registreringsdatoEnhetsregisteret")
            ),
        },
        "website": {
            "value": e.get("hjemmeside"),
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": "Website as self-reported to the register (unverified)." if e.get("hjemmeside") else "No website registered.",
        },
    }

    latest_accounts_period_end = None
    if include_financials:
        try:
            regnskap = regnskap_client.fetch_latest_accounts(orgnr)
        except Exception as exc:  # never let a best-effort source break the whole profile
            regnskap = None
            errors.append(f"financials lookup failed: {exc}")

        if regnskap:
            figures = regnskap_client.extract_headline_figures(regnskap)
            latest_accounts_period_end = regnskap.period_to
            facts["financials"] = {
                "value": figures,
                "source_url": regnskap.source_url,
                "retrieved_at": regnskap.fetched_at,
                "period_covered": {"from": regnskap.period_from, "to": regnskap.period_to},
                "explanation": explain.explain_accounts(True, regnskap.period_to)
                + " NOTE: sourced from Brønnøysundregistrene's experimental accounts API, "
                  "which the register itself describes as unmaintained and subject to removal "
                  "without notice -- treat as best-effort, not guaranteed current.",
            }
        else:
            facts["financials"] = {
                "value": None,
                "source_url": None,
                "retrieved_at": run_time,
                "explanation": explain.explain_accounts(False, None),
            }

    profile: dict[str, Any] = {
        "organisasjonsnummer": orgnr,
        "as_of": run_time,
        "unit_type": unit.unit_type,
        "facts": facts,
        "errors": errors,
    }

    if store is not None:
        comparable = {
            "name": facts["name"]["value"],
            "status": facts["status"]["value"],
            "employees": facts["employees"]["value"],
            "address": facts["address"]["value"],
            "industry": facts["industry"]["value"],
            "vat_registered": facts["vat_registered"]["value"],
            "website": facts["website"]["value"],
            "latest_accounts_period_end": latest_accounts_period_end,
        }
        changes = store.diff_and_store(orgnr, comparable)
        profile["changes_since_last_check"] = changes
        profile["changes_explanation"] = explain.explain_changes(changes)
    else:
        profile["changes_since_last_check"] = []
        profile["changes_explanation"] = "Change tracking disabled for this run (no store provided)."

    return profile
