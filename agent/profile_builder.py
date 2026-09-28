"""
Builds the final company profile: every fact paired with its source URL,
retrieval timestamp, an explicit availability state, and a plain-English
explanation.

STATE VOCABULARY (required, exactly these six literal values):
  available      - we have a real value from the source
  not_available  - source was checked; this field is genuinely empty there
  blocked        - a real HTTP/network failure prevented checking
  not_applicable - this field doesn't exist for this kind of entity
  ambiguous      - we have a value but haven't independently verified it
                   (currently only used for the self-reported website field)
  failed         - an exception was actually caught while fetching this fact

Honesty note on precision: brreg_client / regnskap_client / roller_client
currently collapse "genuinely empty" and "call failed" into a single None
in most cases (by design, to fail safe rather than guess). Where we can
actually tell the difference (an exception was caught, or a real non-200/
non-404 status came back), we report blocked/failed. Where we can't yet
tell the difference, we report not_available rather than overclaiming
blocked/failed -- this is a known, documented simplification, not a bug.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Optional

from . import brreg_client, regnskap_client, roller_client, explain, site_client
from .orgnr import validate, InvalidOrgNumber
from .cache import ProfileStore

VALID_STATES = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}

COMPARABLE_FIELDS = (
    "name", "status", "employees", "address", "industry", "vat_registered",
    "website", "latest_accounts_period_end", "telefon", "epostadresse",
    "registrertKapital", "slettedato",
    "website_identity.url", "website_identity.verified",
)

EVIDENCE_DIR = os.environ.get("SIGNALPOST_EVIDENCE_DIR", "evidence")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _addr(a: Optional[dict]) -> Optional[str]:
    if not a:
        return None
    parts = [", ".join(a.get("adresse") or []), a.get("postnummer"), a.get("poststed"), a.get("land")]
    return ", ".join(p for p in parts if p)


def _fact(value, state, source_url, retrieved_at, explanation, **extra):
    assert state in VALID_STATES, f"invalid state {state!r}"
    d = {"value": value, "state": state, "source_url": source_url,
         "retrieved_at": retrieved_at, "explanation": explanation}
    d.update(extra)
    return d


def _save_evidence(orgnr: str, unit_data: dict,
                    regnskap_latest: Optional[dict] = None,
                    roles_data: Optional[list] = None,
                    subunits_data: Optional[dict] = None,
                    website_data: Optional[dict] = None) -> None:
    """Save raw API responses under evidence/<orgnr>/ for auditability.
    Never raises -- snapshots are a bonus, not a dependency."""
    try:
        company_dir = os.path.join(EVIDENCE_DIR, orgnr)
        snapshot_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        d = os.path.join(company_dir, snapshot_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "enhet.json"), "w", encoding="utf-8") as f:
            json.dump(unit_data, f, ensure_ascii=False, indent=2)
        if regnskap_latest is not None:
            with open(os.path.join(d, "regnskap.json"), "w", encoding="utf-8") as f:
                json.dump(regnskap_latest, f, ensure_ascii=False, indent=2)
        if roles_data is not None:
            with open(os.path.join(d, "roller.json"), "w", encoding="utf-8") as f:
                json.dump(roles_data, f, ensure_ascii=False, indent=2)
        if subunits_data is not None:
            with open(os.path.join(d, "underenheter.json"), "w", encoding="utf-8") as f:
                json.dump(subunits_data, f, ensure_ascii=False, indent=2)
        if website_data is not None:
            with open(os.path.join(d, "website_research.json"), "w", encoding="utf-8") as f:
                json.dump(website_data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def empty_envelope(orgnr_raw: str, state: str, error_message: str) -> dict[str, Any]:
    """
    A minimal, valid, well-formed envelope for inputs that fail before any
    facts can be gathered (invalid syntax, not found, deregistered). This
    guarantees every single input -- valid or not -- produces exactly one
    well-formed JSON envelope, never a bare exception or an ad hoc shape,
    per the "exactly one terminal envelope per input" requirement.
    """
    return {
        "organisasjonsnummer": orgnr_raw,
        "as_of": _now_iso(),
        "unit_type": None,
        "state": state,
        "facts": {},
        "changes_since_last_check": [],
        "changes_explanation": "N/A - lookup did not complete.",
        "errors": [error_message],
    }


def build_profile(
    orgnr_raw: str,
    store: Optional[ProfileStore] = None,
    include_financials: bool = True,
) -> dict[str, Any]:
    """
    Returns a dict with organisasjonsnummer, as_of, unit_type, facts (each
    carrying value/state/source_url/retrieved_at/explanation),
    changes_since_last_check, changes_explanation, and errors.
    Raises InvalidOrgNumber for syntactically bad input.
    """
    orgnr = validate(orgnr_raw)
    run_time = _now_iso()
    errors: list[str] = []

    unit = brreg_client.fetch_unit(orgnr)
    e = unit.data
    su = unit.source_url
    st = unit.fetched_at

    naeringskode1 = e.get("naeringskode1") or {}
    forretningsadresse = e.get("forretningsadresse") or e.get("beliggenhetsadresse")
    org_form = (e.get("organisasjonsform") or {}).get("beskrivelse")
    org_form_kode = (e.get("organisasjonsform") or {}).get("kode")
    status_value = {
        "bankrupt": e.get("konkurs", False),
        "under_liquidation": e.get("underAvvikling", False),
        "under_forced_liquidation": e.get("underTvangsavviklingEllerTvangsopplosning", False),
    }
    addr_str = _addr(forretningsadresse)

    facts: dict[str, Any] = {
        "name": _fact(
            e.get("navn"), "available" if e.get("navn") else "not_available",
            su, st, f"Registered name in the {unit.unit_type} register.",
        ),
        "organisasjonsform": _fact(
            {"code": org_form_kode, "description": org_form},
            "available" if org_form_kode else "not_available", su, st,
            f"Legal organization form: {org_form or 'not reported'} ({org_form_kode or '?'}).",
        ),
        "status": _fact(
            status_value, "available", su, st, explain.explain_status(status_value),
        ),
        "employees": _fact(
            e.get("antallAnsatte"),
            "available" if e.get("antallAnsatte") is not None else "not_available",
            su, st, explain.explain_employees(e.get("antallAnsatte")),
        ),
        "industry": _fact(
            {"code": naeringskode1.get("kode"), "description": naeringskode1.get("beskrivelse")},
            "available" if naeringskode1.get("kode") else "not_available", su, st,
            explain.explain_industry(naeringskode1.get("kode"), naeringskode1.get("beskrivelse")),
        ),
        "address": _fact(
            addr_str, "available" if addr_str else "not_available", su, st,
            "Business address as registered.",
        ),
        "vat_registered": _fact(
            e.get("registrertIMvaregisteret"),
            "available" if e.get("registrertIMvaregisteret") is not None else "not_available",
            su, st, explain.explain_vat(e.get("registrertIMvaregisteret")),
        ),
        "founding_and_registration": _fact(
            {"stiftelsesdato": e.get("stiftelsesdato"),
             "registreringsdato": e.get("registreringsdatoEnhetsregisteret")},
            "available" if (e.get("stiftelsesdato") or e.get("registreringsdatoEnhetsregisteret")) else "not_available",
            su, st,
            explain.explain_founding(e.get("stiftelsesdato"), e.get("registreringsdatoEnhetsregisteret")),
        ),
        "website": _fact(
            e.get("hjemmeside"),
            "ambiguous" if e.get("hjemmeside") else "not_available",
            su, st,
            "Website as self-reported to the register. Marked ambiguous, not available: "
            "we have not independently verified this domain resolves to this exact legal "
            "entity, only that the company itself listed it." if e.get("hjemmeside")
            else "No website registered.",
        ),
        "telefon": _fact(
            e.get("telefon"), "available" if e.get("telefon") else "not_available",
            su, st, "Phone number as recorded in the register." if e.get("telefon")
            else "No phone number recorded in the register.",
        ),
        "epostadresse": _fact(
            e.get("epostadresse"), "available" if e.get("epostadresse") else "not_available",
            su, st, "Email address as recorded in the register." if e.get("epostadresse")
            else "No email address recorded in the register.",
        ),
        "sisteInnsendteAarsregnskap": _fact(
            e.get("sisteInnsendteAarsregnskap"),
            "available" if e.get("sisteInnsendteAarsregnskap") else "not_available",
            su, st,
            f"Most recent fiscal year with filed accounts: {e.get('sisteInnsendteAarsregnskap')}."
            if e.get("sisteInnsendteAarsregnskap")
            else "No fiscal year with filed accounts recorded.",
        ),
    }

    for nk, label_key in (("naeringskode2", "naeringskode2"), ("naeringskode3", "naeringskode3")):
        code = (e.get(nk) or {}).get("kode")
        desc = (e.get(nk) or {}).get("beskrivelse")
        facts[label_key] = _fact(
            f"{code} – {desc}".strip(" –") if code else None,
            "available" if code else "not_available", su, st,
            f"Secondary industry classification (NACE): {code} – {desc}." if code
            else "No secondary industry classification recorded.",
        )

    kapital = e.get("kapital") or {}
    if kapital.get("belop") is not None:
        amount = f"{kapital['belop']:,} {kapital.get('valuta', 'NOK')}"
        facts["registrertKapital"] = _fact(
            amount, "available", su, st,
            f"Share capital registered in the Register of Business Enterprises: {amount}.",
        )
    else:
        facts["registrertKapital"] = _fact(
            None, "not_available", su, st,
            "No registered share capital recorded (normal for entity types without a capital requirement).",
        )

    facts["deletion"] = _fact(
        e.get("slettedato"), "available" if e.get("slettedato") else "not_available", su, st,
        f"This entity was deleted from the register on {e.get('slettedato')}. "
        "Facts reflect the last registered state before deletion." if e.get("slettedato")
        else "No deletion date recorded; entity appears active in the register.",
    )

    # --- Website research: registered website first, discovery when absent ---
    # Brreg remains the identity anchor. If no website is registered, the
    # website client may discover a candidate, but it must independently
    # verify that candidate against this exact organisation number/company.
    website_result = None
    website_value = e.get("hjemmeside")

    try:
        website_result = site_client.research(
            website_value,
            e.get("navn") or "",
            orgnr,
            address=addr_str or "",
            max_pages=4,
            allow_discovery=True,
        )

        web_state = website_result.get("state", "not_available")
        web_source = (
            website_result.get("source_url")
            or website_result.get("official_website")
            or website_value
            or su
        )
        web_time = website_result.get("retrieved_at") or run_time
        verified_url = website_result.get("official_website")

        if web_state == "available" and website_result.get("identity_verified"):
            identity_value = {
                "url": verified_url,
                "verified": True,
                "discovered": bool(website_result.get("discovery_used")),
                "explanation": website_result.get("identity_explanation"),
            }

            facts["website_identity"] = _fact(
                identity_value,
                "available",
                web_source,
                web_time,
                "Official company website independently verified against the exact Brreg organisation/company identity.",
            )

            description = website_result.get("description")
            facts["company_description"] = _fact(
                description,
                "available" if description else "not_available",
                web_source,
                web_time,
                "Description taken from the verified first-party company website; it is not inferred by the agent."
                if description
                else "The verified company website did not expose a usable description.",
            )

            jobs = website_result.get("jobs") or []
            facts["jobs"] = _fact(
                jobs if jobs else None,
                "available" if jobs else "not_available",
                web_source,
                web_time,
                f"Found {len(jobs)} job/careers page(s) on the verified company website."
                if jobs
                else "No job/careers page was found among the checked pages of the verified company website.",
            )

            activity = website_result.get("activity") or []
            facts["public_activity"] = _fact(
                activity if activity else None,
                "available" if activity else "not_available",
                web_source,
                web_time,
                f"Found {len(activity)} public activity/news page(s) on the verified company website."
                if activity
                else "No news, press, blog or activity page was found among the checked pages of the verified company website.",
            )

            checked_pages = website_result.get("pages_checked") or [verified_url]
            facts["website_pages_checked"] = _fact(
                checked_pages,
                "available",
                web_source,
                web_time,
                f"Checked {len(checked_pages)} same-site page(s) during website research.",
            )

        elif web_state == "ambiguous":
            ambiguous_url = website_result.get("source_url") or website_value
            facts["website_identity"] = _fact(
                {
                    "url": ambiguous_url,
                    "verified": False,
                    "discovered": bool(website_result.get("discovery_used")),
                    "explanation": website_result.get("identity_explanation"),
                },
                "ambiguous",
                ambiguous_url or su,
                web_time,
                website_result.get("identity_explanation")
                or "A candidate website was found but could not be confidently linked to this exact company.",
            )
            for field, label in (
                ("company_description", "company description"),
                ("jobs", "jobs"),
                ("public_activity", "public activity"),
            ):
                facts[field] = _fact(
                    None,
                    "ambiguous",
                    ambiguous_url or su,
                    web_time,
                    f"No {label} was published because website identity could not be independently verified.",
                )

        elif web_state == "blocked":
            facts["website_identity"] = _fact(
                {
                    "url": website_result.get("source_url") or website_value,
                    "verified": False,
                    "discovered": bool(website_result.get("discovery_used")),
                    "explanation": website_result.get("identity_explanation"),
                },
                "blocked",
                website_result.get("source_url") or website_value or su,
                web_time,
                website_result.get("identity_explanation")
                or "The public website source was unavailable or refused the request; no unsupported website facts were published.",
            )
            for field, label in (
                ("company_description", "company description"),
                ("jobs", "jobs"),
                ("public_activity", "public activity"),
            ):
                facts[field] = _fact(
                    None,
                    "blocked",
                    website_result.get("source_url") or website_value or su,
                    web_time,
                    f"Website research was blocked; {label} was not guessed.",
                )

        else:
            # No verified website found. This is a legitimate empty result,
            # not a synthetic value and not a reason to fail the whole company.
            facts["website_identity"] = _fact(
                {
                    "url": None,
                    "verified": False,
                    "discovered": bool(website_result.get("discovery_used")),
                    "explanation": website_result.get("identity_explanation"),
                },
                "not_available",
                website_result.get("source_url") or website_value or su,
                web_time,
                website_result.get("identity_explanation")
                or "No verified official company website was found.",
            )
            for field, label in (
                ("company_description", "company description"),
                ("jobs", "jobs"),
                ("public_activity", "public activity"),
            ):
                facts[field] = _fact(
                    None,
                    "not_available",
                    website_result.get("source_url") or website_value or su,
                    web_time,
                    f"No verified company website was available, so {label} could not be established from a first-party site.",
                )

        # Only surface website errors that came from a registered website.
        # Speculative discovery candidate failures are deliberately suppressed
        # by site_client so one dead candidate does not mark a good profile bad.
        if website_value and website_result.get("errors"):
            errors.extend(
                f"website: {x}"
                for x in website_result["errors"][:5]
            )

    except Exception as exc:
        errors.append(f"website research failed: {exc}")
        facts["website_identity"] = _fact(
            {
                "url": website_value,
                "verified": False,
                "discovered": False,
            },
            "failed",
            website_value or su,
            run_time,
            "Website research failed; no unsupported website facts were published.",
        )
        for field, label in (
            ("company_description", "company description"),
            ("jobs", "jobs"),
            ("public_activity", "public activity"),
        ):
            facts[field] = _fact(
                None,
                "failed",
                website_value or su,
                run_time,
                f"Website research failed; {label} was not guessed.",
            )

    # --- Roles: structurally only exist for hovedenhet ---
    roles = None
    if unit.unit_type == "underenhet":
        facts["roles"] = _fact(
            None, "not_applicable", None, run_time,
            "Sub-units do not carry their own registered roles; roles belong to the parent main unit.",
        )
    else:
        try:
            roles = roller_client.fetch_roles(orgnr)
            facts["roles"] = _fact(
                roles, "available" if roles else "not_available",
                f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller", run_time,
                f"{len(roles)} current role(s) registered (board, CEO, auditor, etc.). Resigned roles excluded."
                if roles else "No current roles found in the roles register for this entity.",
            )
        except Exception as exc:
            errors.append(f"roles lookup failed: {exc}")
            facts["roles"] = _fact(
                None, "failed", None, run_time,
                "Could not retrieve roles due to a source error; not the same as having none.",
            )

    # --- Registered workplaces (sub-units): only meaningful for hovedenhet ---
    workplaces_result = None
    if unit.unit_type == "underenhet":
        facts["registered_workplaces"] = _fact(
            None, "not_applicable", None, run_time,
            "Sub-units do not themselves have registered workplaces; this concept applies to main units only.",
        )
    else:
        try:
            workplaces_result = brreg_client.fetch_subunits(orgnr)
            units = workplaces_result.data.get("_embedded", {}).get("underenheter", [])
            workplaces = [
                {
                    "organisasjonsnummer": u.get("organisasjonsnummer"),
                    "name": u.get("navn"),
                    "address": _addr(u.get("beliggenhetsadresse") or u.get("forretningsadresse")),
                }
                for u in units
            ]
            facts["registered_workplaces"] = _fact(
                workplaces if workplaces else None,
                "available" if workplaces else "not_available",
                workplaces_result.source_url, workplaces_result.fetched_at,
                f"{len(workplaces)} registered workplace(s) (underenheter) found for this main unit."
                if workplaces else "No registered sub-unit workplaces found (common for single-location companies).",
            )
        except Exception as exc:
            errors.append(f"registered workplaces lookup failed: {exc}")
            facts["registered_workplaces"] = _fact(
                None, "failed", None, run_time,
                "Could not retrieve registered workplaces due to a source error; not the same as having none.",
            )

    # --- Financials: best-effort, verbatim, never estimated ---
    latest_accounts_period_end = None
    regnskap = None
    if include_financials:
        financials_failed = False
        try:
            regnskap = regnskap_client.fetch_latest_accounts(orgnr)
        except Exception as exc:
            financials_failed = True
            errors.append(f"financials lookup failed: {exc}")

        if regnskap:
            figures = regnskap_client.extract_headline_figures(regnskap)
            latest_accounts_period_end = regnskap.period_to
            facts["financials"] = _fact(
                figures, "available", regnskap.source_url, regnskap.fetched_at,
                explain.explain_accounts(True, regnskap.period_to)
                + " NOTE: sourced from Brønnøysundregistrene's experimental accounts API, "
                  "which the register itself describes as unmaintained and subject to removal "
                  "without notice -- treat as best-effort, not guaranteed current.",
                period_covered={"from": regnskap.period_from, "to": regnskap.period_to},
            )
        elif financials_failed:
            facts["financials"] = _fact(
                None, "failed", None, run_time,
                "An error occurred while querying the accounts register; not the same as no accounts existing.",
            )
        else:
            facts["financials"] = _fact(
                None, "not_available", None, run_time, explain.explain_accounts(False, None),
            )

    _save_evidence(
        orgnr, e,
        regnskap.latest if regnskap else None,
        roles,
        workplaces_result.data if workplaces_result else None,
        website_result,
    )

    profile: dict[str, Any] = {
        "organisasjonsnummer": orgnr,
        "as_of": run_time,
        "unit_type": unit.unit_type,
        "state": "available",
        "facts": facts,
        "errors": errors,
    }

    if store is not None:
        website_identity_value = facts.get("website_identity", {}).get("value") or {}

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
            # Keep website identity tracking flat. The cache comparator treats
            # nested dictionaries as both a parent and child, which can produce
            # false changes such as {"old": null, "new": null}.
            "website_identity.url": website_identity_value.get("url"),
            "website_identity.verified": website_identity_value.get("verified"),
            "company_description": facts.get("company_description", {}).get("value"),
            "jobs": facts.get("jobs", {}).get("value"),
            "public_activity": facts.get("public_activity", {}).get("value"),
        }
        changes = store.diff_and_store(orgnr, comparable)
        profile["changes_since_last_check"] = changes
        profile["changes_explanation"] = explain.explain_changes(changes)
    else:
        profile["changes_since_last_check"] = []
        profile["changes_explanation"] = "Change tracking disabled for this run (no store provided)."

    return profile