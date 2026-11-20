from datetime import date
from unittest.mock import Mock

import pytest

from app.cli import _print_owner
from app.models import DomainError
from app.owner import owner_agenda
from tests.conftest import sample_booking, sample_catalog


def test_owner_agenda_prints_bookings_with_one_catalog_read(capsys):
    repository = Mock()
    repository.catalog.return_value = sample_catalog()
    repository.list_owner_bookings.return_value = [sample_booking()]

    _print_owner(repository, "demo-studio")

    assert capsys.readouterr().out == (
        "\nAgenda: Demo Studio (UTC)\n"
        "  2099-01-05 15:00 scheduled Ada Lovelace service=Massage\n"
    )
    repository.catalog.assert_called_once_with("demo-studio")


def test_owner_agenda_rejects_invalid_date_ranges():
    with pytest.raises(DomainError, match="Date range"):
        owner_agenda(Mock(), sample_catalog(), date(2026, 2, 1), date(2026, 1, 1))


def test_owner_agenda_prints_when_there_are_no_bookings(capsys):
    repository = Mock()
    repository.catalog.return_value = sample_catalog()
    repository.list_owner_bookings.return_value = []

    _print_owner(repository, "demo-studio")

    assert capsys.readouterr().out == "\nAgenda: Demo Studio (UTC)\n  no bookings\n"
    repository.catalog.assert_called_once_with("demo-studio")
