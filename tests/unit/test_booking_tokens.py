from types import SimpleNamespace

import pytest

from app.booking import BookingService, _digest, _request_hash
from app.models import DomainError


def service(secret="a" * 32):
    return BookingService(SimpleNamespace(database_url="postgresql://unused"), secret)


def test_management_token_is_signed_and_only_digest_is_persistable():
    booking = service()

    token = booking._token("123e4567-e89b-12d3-a456-426614174000", 2)
    booking_id, version, digest = booking._token_claims(token)

    assert booking_id == "123e4567-e89b-12d3-a456-426614174000"
    assert version == 2
    assert digest == _digest(token)
    assert token not in digest


def test_tampered_or_malformed_token_is_indistinguishable_from_missing_booking():
    booking = service()
    valid = booking._token("123e4567-e89b-12d3-a456-426614174000", 1)

    for token in (valid[:-1] + "0", "not-a-token"):
        with pytest.raises(DomainError) as error:
            booking._token_claims(token)
        assert error.value.code == "booking_not_found"
        assert error.value.status_code == 404


def test_management_is_unavailable_without_a_strong_secret():
    booking = service("too-short")

    with pytest.raises(DomainError) as error:
        booking._token("booking-1", 1)

    assert error.value.code == "booking_token_not_configured"
    assert error.value.status_code == 503


def test_idempotency_hash_is_stable_across_dictionary_order():
    assert _request_hash({"a": 1, "b": 2}) == _request_hash({"b": 2, "a": 1})
