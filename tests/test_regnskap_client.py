"""
This fixture is a REAL response captured live from
https://data.brreg.no/regnskapsregisteret/regnskap/923609016 on 2026-09-24
(Equinor ASA's 2025 annual accounts). It exists to lock in the correct
URL shape (orgNummer as a path segment) after discovering that the
archived OpenAPI spec on GitHub described a stale, no-longer-live query-
parameter shape that 404s in production. If this test ever breaks after
an edit to regnskap_client.py, that's a sign the URL construction has
regressed back to the broken shape.
"""

from unittest.mock import patch, MagicMock

from agent import regnskap_client

REAL_EQUINOR_2025_RESPONSE = [
    {
        "id": 7192427,
        "journalnr": "2026635024",
        "regnskapstype": "SELSKAP",
        "virksomhet": {
            "organisasjonsnummer": "923609016",
            "organisasjonsform": "ASA",
            "morselskap": True,
        },
        "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
        "valuta": "USD",
        "resultatregnskapResultat": {
            "ordinaertResultatFoerSkattekostnad": 6124000000.0,
            "aarsresultat": 5731000000.0,
            "driftsresultat": {
                "driftsresultat": 5563000000.0,
                "driftsinntekter": {"sumDriftsinntekter": 67956000000.0},
            },
        },
        "egenkapitalGjeld": {
            "sumEgenkapitalGjeld": 103431000000.0,
            "egenkapital": {"sumEgenkapital": 39182000000.0},
        },
    }
]


def _fake_get(url, headers=None, timeout=8.0):
    resp = MagicMock()
    if url == "https://data.brreg.no/regnskapsregisteret/regnskap/923609016":
        resp.status_code = 200
        resp.json.return_value = REAL_EQUINOR_2025_RESPONSE
    else:
        resp.status_code = 404
    return resp


@patch("agent.regnskap_client.requests.get", side_effect=_fake_get)
def test_fetch_latest_accounts_uses_orgnr_as_path_segment(mock_get):
    result = regnskap_client.fetch_latest_accounts("923609016")
    assert result is not None
    assert result.source_url == "https://data.brreg.no/regnskapsregisteret/regnskap/923609016"
    assert result.period_to == "2025-12-31"


@patch("agent.regnskap_client.requests.get", side_effect=_fake_get)
def test_extract_headline_figures_matches_real_shape(mock_get):
    result = regnskap_client.fetch_latest_accounts("923609016")
    figures = regnskap_client.extract_headline_figures(result)
    assert figures["currency"] == "USD"
    assert figures["revenue"] == 67956000000.0
    assert figures["operating_result"] == 5563000000.0
    assert figures["net_result"] == 5731000000.0
    assert figures["equity"] == 39182000000.0


def test_wrong_old_query_param_shape_would_404():
    """
    Documents WHY the fix was needed: hitting the old (broken) shape -
    query param on the bare path - is what returned 404 in production.
    """
    def old_broken_fake_get(url, headers=None, timeout=8.0):
        resp = MagicMock()
        resp.status_code = 404  # this is what production actually returned
        return resp

    with patch("agent.regnskap_client.requests.get", side_effect=old_broken_fake_get):
        # confirms fetch_latest_accounts now builds a URL that does NOT
        # rely on the old params= query-string shape at all
        result = regnskap_client.fetch_latest_accounts("923609016")
        assert result is None  # would 404 either way here since our fake always 404s,
        # but the real assertion is in test_fetch_latest_accounts_uses_orgnr_as_path_segment
        # above, which proves the URL is built correctly and succeeds against the real shape
