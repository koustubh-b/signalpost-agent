"""
Turns raw register fields into one-line, plain-English explanations.

This is deliberately template-based rather than LLM-generated: it is free,
deterministic, instant, and -- crucially for the "give useful explanations"
criterion -- every sentence is directly traceable to a specific field the
register actually returned, so there's no risk of an LLM adding a plausible
but unsupported detail. An optional LLM polish pass (see llm_polish.py) can
be layered on top for tone, but the underlying facts always come from here.
"""

from __future__ import annotations

from typing import Any, Optional


def explain_status(facts: dict[str, Any]) -> str:
    if facts.get("bankrupt"):
        return "Registered as bankrupt (konkurs) in the Central Coordinating Register."
    if facts.get("under_forced_liquidation"):
        return "Registered as under forced winding-up or dissolution."
    if facts.get("under_liquidation"):
        return "Registered as under voluntary liquidation (avvikling)."
    return "No bankruptcy, forced liquidation, or dissolution flags are set in the register."


def explain_employees(count: Optional[int]) -> str:
    if count is None:
        return "Employee count not reported to the register (common for holding entities or very small units)."
    if count == 0:
        return "Register reports zero employees as of the last update."
    return f"Register reports {count} employee(s) as of the last update."


def explain_industry(kode: Optional[str], beskrivelse: Optional[str]) -> str:
    if not kode:
        return "No NACE industry code registered."
    return f"Classified under NACE code {kode} ({beskrivelse or 'no description provided'})."


def explain_vat(registered: Optional[bool]) -> str:
    if registered is None:
        return "VAT registration status not reported."
    return "Registered in the VAT register (Merverdiavgiftsregisteret)." if registered else \
        "Not currently registered in the VAT register."

def explain_founding(stiftelsesdato: Optional[str], registreringsdato: Optional[str]) -> str:
    parts = []
    if stiftelsesdato:
        parts.append(f"founded {stiftelsesdato}")
    if registreringsdato:
        parts.append(f"registered in the Central Coordinating Register {registreringsdato}")
    return ("Company " + ", ".join(parts) + ".") if parts else "Founding/registration dates not reported."


def explain_accounts(has_accounts: bool, period_to: Optional[str]) -> str:
    if not has_accounts:
        return "No annual accounts found in the accounts register (may not yet be due, or entity type is exempt)."
    return f"Most recent submitted annual accounts cover the period ending {period_to}."


def explain_changes(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "No changes detected since the last check of this company."
    fields = ", ".join(sorted({c["field"] for c in changes}))
    return f"{len(changes)} field(s) changed since the last check: {fields}."
