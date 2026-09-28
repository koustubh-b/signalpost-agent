"""Regression tests for regnskap_client.

Pins the two things that have broken in production before:

1. orgnr must be a PATH segment:
       /regnskap/{orgnr}

2. extract_headline_figures must handle the real response nesting
   and must omit absent fields rather than zero-filling them.
"""

from __future__ import annotations

from unittest.mock import patch

from agent.regnskap_client import (
    fetch_latest_accounts,
    extract_headline_figures,
)


ORG = "923609016"

EXPECTED_URL = (
    "https://data.brreg.no/"
    "regnskapsregisteret/regnskap/923609016"
)


# Shape verified against Equinor's real 2025 filing.
FIXTURE = [
    {
        "regnskapsperiode": {
            "fraDato": "2024-01-01",
            "tilDato": "2024-12-31",
        },

        "valuta": "NOK",

        "resultatregnskapResultat": {
            "driftsresultat": {
                "driftsinntekter": {
                    "sumDriftsinntekter": 997000000000,
                },

                "driftskostnader": {
                    "sumDriftskostnader": 850000000000,
                },

                "driftsresultat": 147000000000,
            },

            "aarsresultat": 100000000000,
        },

        "egenkapitalGjeld": {
            "egenkapital": {
                "sumEgenkapital": 500000000000,
            },

            "gjeld": {
                "sumGjeld": 700000000000,
            },
        },

        "eiendeler": {
            "sumEiendeler": 1200000000000,
        },

        "loennOpplysninger": {
            "loennskostnader": 50000000000,
        },
    }
]


def test_fetch_uses_orgnr_as_path_segment():
    captured = {}

    def fake_get_json(url, timeout=8.0):
        captured["url"] = url
        return FIXTURE

    with patch(
        "agent.regnskap_client.get_json",
        side_effect=fake_get_json,
    ):
        result = fetch_latest_accounts(ORG)

    assert result is not None

    assert captured["url"] == EXPECTED_URL

    # Old broken query-param format must never return.
    assert "?orgNummer" not in captured["url"]

    assert result.period_from == "2024-01-01"
    assert result.period_to == "2024-12-31"


def test_fetch_returns_none_when_unavailable():

    with patch(
        "agent.regnskap_client.get_json",
        return_value=None,
    ):
        assert fetch_latest_accounts(ORG) is None

    with patch(
        "agent.regnskap_client.get_json",
        return_value=[],
    ):
        assert fetch_latest_accounts(ORG) is None


def test_extract_matches_real_shape():

    with patch(
        "agent.regnskap_client.get_json",
        return_value=FIXTURE,
    ):
        result = fetch_latest_accounts(ORG)

    figures = extract_headline_figures(result)

    assert figures["revenue"] == 997000000000

    assert figures["operating_expenses"] == 850000000000

    assert figures["operating_result"] == 147000000000

    assert figures["net_result"] == 100000000000

    assert figures["equity"] == 500000000000

    assert figures["liabilities"] == 700000000000

    assert figures["assets"] == 1200000000000

    assert figures["wage_costs"] == 50000000000


def test_absent_fields_are_omitted_not_zero_filled():

    sparse = [
        {
            "regnskapsperiode": {
                "fraDato": "2024-01-01",
                "tilDato": "2024-12-31",
            },

            "resultatregnskapResultat": {
                "driftsresultat": {
                    "driftsinntekter": {
                        "sumDriftsinntekter": 1000,
                    }
                }
            },
        }
    ]

    with patch(
        "agent.regnskap_client.get_json",
        return_value=sparse,
    ):
        result = fetch_latest_accounts(ORG)

    figures = extract_headline_figures(result)

    assert figures["revenue"] == 1000

    # Missing in source -> must NOT appear as zero.
    assert "wage_costs" not in figures


def test_old_query_param_shape_is_never_used():
    """
    Documents the original production bug.

    The old query-param format returned 404.
    The organisation number must remain a URL path segment.
    """

    captured = {}

    def fake_get_json(url, timeout=8.0):
        captured["url"] = url
        return FIXTURE

    with patch(
        "agent.regnskap_client.get_json",
        side_effect=fake_get_json,
    ):
        fetch_latest_accounts(ORG)

    assert captured["url"].endswith(f"/{ORG}")

    assert "?" not in captured["url"]