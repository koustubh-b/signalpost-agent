"""
Since the sandbox this was built in can't reach data.brreg.no directly,
this test mocks the Brreg/financial/website layers with responses shaped exactly per the
official OpenAPI spec (component schema `Enhet`), so the parsing logic
is verified even without live network access. Run this for real against
the live API once you have it (see README) to do an end-to-end check.
"""

from unittest.mock import patch, MagicMock

from agent.profile_builder import build_profile
from agent.cache import ProfileStore

FIXTURE_ENHET = {
    "organisasjonsnummer": "923609016",
    "navn": "EQUINOR ASA",
    "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
    "registreringsdatoEnhetsregisteret": "1995-05-19",
    "registrertIMvaregisteret": True,
    "naeringskode1": {"kode": "06.100", "beskrivelse": "Utvinning av råolje"},
    "antallAnsatte": 21500,
    "stiftelsedato": "1972-06-14",
    "forretningsadresse": {
        "adresse": ["Forusbeen 50"],
        "postnummer": "4035",
        "poststed": "STAVANGER",
        "land": "Norge",
    },
    "konkurs": False,
    "underAvvikling": False,
    "underTvangsavviklingEllerTvangsopplosning": False,
    "hjemmeside": "www.equinor.com",
}


def _fake_get(url, params=None, timeout=10.0):
    resp = MagicMock()
    if url.endswith("/enheter/923609016"):
        resp.status_code = 200
        resp.json.return_value = FIXTURE_ENHET
    else:
        resp.status_code = 404
    return resp


@patch("agent.brreg_client._get", side_effect=_fake_get)
@patch("agent.regnskap_client.fetch_latest_accounts", return_value=None)
@patch(
    "agent.site_client.research",
    return_value={
        "state": "not_available",
        "source_url": FIXTURE_ENHET["hjemmeside"],
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "identity_verified": False,
        "discovery_used": False,
        "identity_explanation": "Website research is mocked in unit tests.",
        "official_website": None,
        "description": None,
        "jobs": [],
        "activity": [],
        "pages_checked": [],
        "errors": [],
    },
)
def test_build_profile_happy_path(mock_site, mock_regnskap, mock_get, tmp_path):
    store = ProfileStore(str(tmp_path / "test_cache.db"))
    profile = build_profile("923609016", store=store, include_financials=True)

    assert profile["organisasjonsnummer"] == "923609016"
    assert profile["facts"]["name"]["value"] == "EQUINOR ASA"
    assert profile["facts"]["name"]["source_url"].endswith("/enheter/923609016")
    assert profile["facts"]["employees"]["value"] == 21500
    assert profile["facts"]["status"]["value"]["bankrupt"] is False
    assert "No bankruptcy" in profile["facts"]["status"]["explanation"]
    assert profile["facts"]["industry"]["value"]["code"] == "06.100"
    # first run: no prior snapshot, so no changes yet
    assert profile["changes_since_last_check"] == []
    store.close()


@patch("agent.brreg_client._get", side_effect=_fake_get)
@patch("agent.regnskap_client.fetch_latest_accounts", return_value=None)
@patch(
    "agent.site_client.research",
    return_value={
        "state": "not_available",
        "source_url": FIXTURE_ENHET["hjemmeside"],
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "identity_verified": False,
        "discovery_used": False,
        "identity_explanation": "Website research is mocked in unit tests.",
        "official_website": None,
        "description": None,
        "jobs": [],
        "activity": [],
        "pages_checked": [],
        "errors": [],
    },
)
def test_change_detection_on_second_run(mock_site, mock_regnskap, mock_get, tmp_path):
    db_path = str(tmp_path / "test_cache.db")
    store = ProfileStore(db_path)
    build_profile("923609016", store=store, include_financials=True)

    # simulate a real-world change: employee count updated
    changed = dict(FIXTURE_ENHET)
    changed["antallAnsatte"] = 21600

    def _fake_get_v2(url, params=None, timeout=10.0):
        resp = MagicMock()
        if url.endswith("/enheter/923609016"):
            resp.status_code = 200
            resp.json.return_value = changed
        else:
            resp.status_code = 404
        return resp

    with patch("agent.brreg_client._get", side_effect=_fake_get_v2):
        profile2 = build_profile("923609016", store=store, include_financials=True)

    assert any(c["field"] == "employees" for c in profile2["changes_since_last_check"])
    store.close()
