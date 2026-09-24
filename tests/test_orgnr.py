from agent.orgnr import is_valid, validate, normalize, InvalidOrgNumber
import pytest


def test_known_valid_orgnr():
    # Equinor ASA - real, publicly registered orgnr
    assert is_valid("923609016")
    assert validate("923 609 016") == "923609016"


def test_normalize_strips_formatting():
    assert normalize("923 609 016") == "923609016"
    assert normalize("NO923609016MVA") == "923609016"


def test_wrong_length_rejected():
    assert not is_valid("12345")
    with pytest.raises(InvalidOrgNumber):
        validate("12345")


def test_bad_checksum_rejected():
    # last digit tampered with
    assert not is_valid("923609017")


def test_non_digit_rejected():
    assert not is_valid("abcdefghi")
