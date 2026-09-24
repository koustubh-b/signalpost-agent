# Signalpost Agent

Given a Norwegian organisasjonsnummer (org number), this agent returns
company facts, each tied to an exact source URL and retrieval date, and
tracks what changed since the last time it checked.

## Why this approach

The two ways this challenge is explicitly failed are a **wrong-company
publication** and a **fabricated financial value**. Both risks come from
treating this as a generic "search the web and guess" problem. Norway
doesn't require that: every company has a unique organisasjonsnummer, and
the Norwegian government's own company register
([Brønnøysundregistrene](https://www.brreg.no/), "Brreg") lets you look a
company up **directly by that number**, for free, with no auth, under an
open license (NLOD).

So the agent's core source of truth is a single authoritative government
API, not a fuzzy multi-source web search:

| Fact | Source | Risk of wrong match |
|---|---|---|
| Name, status, address, industry code, employees, VAT registration, founding/registration dates | [Enhetsregisteret](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html) (Central Coordinating Register) | Effectively zero — looked up by exact orgnr, which the register itself uses as the unique key |
| Change feed | Enhetsregisteret's own `/oppdateringer/enheter` endpoint | N/A — this *is* the register telling you what it changed |
| Revenue, operating result, net result, equity | [Regnskapsregisteret](https://data.brreg.no/regnskapsregisteret/regnskap) (accounts register) | Low, but this endpoint is explicitly documented by Brreg as an unmaintained R&D API that "may be taken down without notice" — see `agent/regnskap_client.py`. Treated as strictly best-effort: on any failure, financials are omitted, never guessed or estimated. |

No financial figure is ever computed or inferred by the agent itself — only
values returned verbatim by the source API are surfaced, each with its
source URL and the exact accounting period it covers.

## Setup

```bash
pip install -r requirements.txt
```

Requires Python 3.10+.

## One command to run it

Single company:
```bash
python -m agent.cli lookup 923609016
```

Batch (the required 1,000+ profiles):
```bash
# pull 1,000 real, currently-registered org numbers from the register
python -m agent.cli sample --count 1000 --out sample_orgnrs.txt

# look all of them up, writing one JSON profile per line
python -m agent.cli batch sample_orgnrs.txt --out profiles.jsonl
```

To re-run against the *same* companies later and see what changed
(this is what the daily grader effectively does), just run `batch` again
against the same input file — the local SQLite cache (`signalpost_cache.db`)
diffs against the previous run automatically and each profile's
`changes_since_last_check` field is populated.

## Output shape

```json
{
  "organisasjonsnummer": "923609016",
  "as_of": "2026-09-24T10:00:00+00:00",
  "unit_type": "hovedenhet",
  "facts": {
    "name": {
      "value": "EQUINOR ASA",
      "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
      "retrieved_at": "2026-09-24T10:00:00+00:00",
      "explanation": "Registered name in the hovedenhet register."
    },
    "status": { "...": "bankrupt/liquidation flags, with a plain-English explanation" },
    "employees": { "...": "..." },
    "industry": { "...": "NACE code + description" },
    "address": { "...": "..." },
    "vat_registered": { "...": "..." },
    "founding_and_registration": { "...": "..." },
    "website": { "...": "self-reported, marked unverified" },
    "financials": { "...": "best-effort, omitted if unavailable, flagged as experimental source" }
  },
  "changes_since_last_check": [
    {"field": "employees", "old_value": 21500, "new_value": 21600}
  ],
  "changes_explanation": "1 field(s) changed since the last check: employees.",
  "errors": []
}
```

Every fact carries its own `source_url` and `retrieved_at` — this is the
"evidence" the grading rubric asks for, at the field level rather than
just the profile level.

## How this maps to the scoring rubric

- **Find useful information (35 pts):** eight core facts pulled straight
  from the authoritative register, plus best-effort financials.
- **Match it correctly with evidence (30 pts):** lookup is by exact
  organisasjonsnummer against the entity that number identifies, by
  construction of the register — not a name-similarity search. Every fact
  cites the exact endpoint and timestamp it came from.
- **Update correctly (20 pts):** every run hits the live API (never serves
  cache instead of a fresh check), diffs against the last stored snapshot,
  and reports exactly what changed and when.
- **Give useful explanations (10 pts):** every fact ships with a one-line,
  template-generated (not LLM-hallucinated) explanation traceable to the
  specific field it describes.
- **Easy to use (5 pts):** one CLI command for a single lookup, one for a
  full batch.

## Model / API details

- **No LLM is required or used for core facts** — all data comes directly
  from Brønnøysundregistrene's own APIs (Enhetsregisteret + Regnskapsregisteret),
  parsed deterministically. This eliminates hallucination risk on the facts
  that are scored most heavily (usefulness + evidence + update correctness).
- Both APIs are free, require no API key, and are licensed under NLOD
  (Norwegian Licence for Open Government Data), so they're a "permitted
  public source" with zero per-call cost.
- If you want LLM-polished prose explanations layered on top of the
  template ones (purely cosmetic — the underlying facts don't change),
  that's a natural extension point in `agent/explain.py`; it was left out
  of this submission to keep run cost at $0 and avoid introducing any
  wording not directly traceable to a register field.

## Expected run costs (against the daily 45 min / 2,000 requests / $10 limits)

Per company: 1 request to Enhetsregisteret (2 if the orgnr is a sub-unit,
since the main-unit endpoint is tried first) + 1 request to Regnskapsregisteret
= **2–3 HTTP requests/company**.

For the daily 100-company test: **~200–300 requests**, well under the
2,000/day cap, and **$0 in API cost**, since both sources are free.
At `--sleep 0.05` between requests, 100 companies complete in well under
a minute, comfortably inside the 45-minute window. The `sample`/`batch`
commands used to build the required 1,000-profile submission are also
$0 — same free sources, just more requests.

## Repository layout

```
agent/
  orgnr.py            # mod-11 checksum validation (rejects bad input before any API call)
  brreg_client.py      # Enhetsregisteret client (main source of truth)
  regnskap_client.py   # best-effort accounts-register client
  cache.py             # SQLite snapshot store + diff engine ("update correctly")
  explain.py            # template-based, per-field explanations
  profile_builder.py   # orchestrates everything into one evidenced profile
  cli.py                # `lookup` / `batch` / `sample` commands
tests/
  test_orgnr.py          # checksum validation, including a real orgnr (Equinor)
  test_profile_builder.py # mocked end-to-end profile build + change detection
```

## Known limitations / honesty notes for the submission

- The accounts-register API (`regnskap_client.py`) is explicitly flagged
  by Brreg as an unmaintained R&D endpoint. If it's ever removed, financials
  simply stop appearing in profiles (`facts.financials.value == null`) —
  the agent degrades gracefully rather than breaking or fabricating numbers.
  (During live testing on 2026-09-24, the archived OpenAPI spec published on
  GitHub turned out to be stale — it described `orgNummer` as a query
  parameter on the bare `/regnskap` path, which 404s in production. The real,
  currently-live shape is `GET /regnskapsregisteret/regnskap/{orgNummer}`,
  confirmed against the live `/v3/api-docs` and a real response for Equinor's
  2025 filing. `tests/test_regnskap_client.py` locks this in with a
  regression test using that real captured response, precisely because this
  API has already changed shape once without the archived docs catching up.)
- `website` is self-reported by the company to the register and is
  surfaced as unverified — it is not fetched or checked for liveness.
- Sub-units (`underenheter`) are supported (many Norwegian org numbers you
  encounter, e.g. individual retail locations of a chain, are sub-units of
  a parent company) — the client tries the main-unit endpoint first and
  falls back automatically.
