"""
Builds the final company profile: every fact paired with its source URL,
retrieval timestamp, and a plain-English explanation. This shape is what
gets scored on "find useful information", "match correctly with evidence",
"update correctly", and "give useful explanations".
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Optional

from . import brreg_client, regnskap_client, explain, roller_client
from .orgnr import validate, InvalidOrgNumber
from .cache import ProfileStore

# Fields we track for change-detection. Deliberately excludes fields that
# are expected to be noisy/irrelevant (like fetch timestamps themselves).
COMPARABLE_FIELDS = (
    "name", "status", "employees", "address", "industry", "vat_registered",
    "website", "latest_accounts_period_end",
    "telefon", "epostadresse", "registrertKapital", "slettedato",
)

EVIDENCE_DIR = os.environ.get("SIGNALPOST_EVIDENCE_DIR", "evidence")


def _addr(a: Optional[dict]) -> Optional[str]:
    if not a:
        return None
    parts = [", ".join(a.get("adresse") or []), a.get("postnummer"), a.get("poststed"), a.get("land")]
    return ", ".join(p for p in parts if p)


def _save_evidence(orgnr: str, unit_data: dict,
                   regnskap_latest: Optional[dict] = None,
                   roles_data: Optional[list] = None) -> None:
    """Save raw API responses under evidence/<orgnr>/ for auditability.
    Never raises -- snapshots are a bonus, not a dependency."""
    try:
        d = os.path.join(EVIDENCE_DIR, orgnr)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "enhet.json"), "w", encoding="utf-8") as f:
            json.dump(unit_data, f, ensure_ascii=False, indent=2)
        if regnskap_latest is not None:
            with open(os.path.join(d, "regnskap.json"), "w", encoding="utf-8") as f:
                json.dump(regnskap_latest, f, ensure_ascii=False, indent=2)
        if roles_data is not None:
            with open(os.path.join(d, "roller.json"), "w", encoding="utf-8") as f:
                json.dump(roles_data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _expanded_unit_facts(e: dict, source_url: str, fetched_at: str) -> dict:
    """Every additional directly-stated register field. Absent -> omitted,
    never guessed. Inline explanations keep this self-contained."""
    facts: dict[str, Any] = {}

    def _fact(value, explanation):
        return {"value": value, "source_url": source_url,
                "retrieved_at": fetched_at, "explanation": explanation}

    for key, label in (
        ("telefon", "Phone number"),
        ("epostadresse", "Email address"),
        ("sisteInnsendteAarsregnskap", "Most recent fiscal year with filed accounts"),
    ):
        if e.get(key):
            facts[key] = _fact(e[key], f"{label} as recorded in the register.")

    for nk in ("naeringskode2", "naeringskode3"):
        code = (e.get(nk) or {}).get("kode")
        if code:
            desc = (e.get(nk) or {}).get("beskrivelse") or "no description"
            facts[nk] = _fact(
                f"{code} – {desc}".strip(" –"),
                f"Secondary industry classification (NACE): {code} – {desc}.",
            )

    kapital = e.get("kapital") or {}
    if kapital.get("belop"):
        valuta = kapital.get("valuta", "NOK")
        amount = f"{kapital['belop']:,} {valuta}"
        facts["registrertKapital"] = _fact(
            amount,
            f"Share capital registered in the Register of Business Enterprises: {amount}.",
        )

    if e.get("slettedato"):
        facts["deletion"] = _fact(
            e["slettedato"],
            f"This entity was deleted from the register on {e['slettedato']}. "
            "Facts reflect the last registered state before deletion.",
        )

    return facts


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
                "stiftelsesdato": e.get("stiftelsesdato"),
                "registreringsdato": e.get("registreringsdatoEnhetsregisteret"),
            },
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": explain.explain_founding(
                e.get("stiftelsesdato"), e.get("registreringsdatoEnhetsregisteret")
            ),
        },
        "website": {
            "value": e.get("hjemmeside"),
            "source_url": unit.source_url,
            "retrieved_at": unit.fetched_at,
            "explanation": "Website as self-reported to the register (unverified)." if e.get("hjemmeside") else "No website registered.",
        },
    }

    # --- Enrichment: extra register facts + deleted-entity guard ---
    facts.update(_expanded_unit_facts(e, unit.source_url, unit.fetched_at))

    # --- Roles: only main units have them; best-effort, omitted on failure ---
    roles = None
    if unit.unit_type == "hovedenhet":
        roles = roller_client.fetch_roles(orgnr)
        if roles:
            facts["roles"] = {
                "value": roles,
                "source_url": f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller",
                "retrieved_at": run_time,
                "explanation": (
                    f"{len(roles)} current role(s) registered (board, CEO, auditor, etc.). "
                    "Resigned roles are excluded; source is the roles register only."
                ),
            }

    latest_accounts_period_end = None
    regnskap = None
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

    # --- Evidence snapshots: raw API responses saved for auditability ---
    _save_evidence(orgnr, e,
                   regnskap.latest if (include_financials and regnskap) else None,
                   roles)

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
            "telefon": e.get("telefon"),
            "epostadresse": e.get("epostadresse"),
            "registrertKapital": (e.get("kapital") or {}).get("belop"),
            "slettedato": e.get("slettedato"),
        }
        changes = store.diff_and_store(orgnr, comparable)
        profile["changes_since_last_check"] = changes
        profile["changes_explanation"] = explain.explain_changes(changes)
    else:
        profile["changes_since_last_check"] = []
        profile["changes_explanation"] = "Change tracking disabled for this run (no store provided)."

    return profile