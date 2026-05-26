"""
Tests for the ETL layer (etl/ingest.py and etl/economics.py)

Coverage:
  - _parse_covid_row: valid row, missing fields, negative values
  - _normalize_dose: all dose label variants
  - _bcb_date_str / _bcb_parse_date: round-trip
  - _sidra_parse_period: monthly and quarterly formats
  - aggregate_covid_monthly: correct monthly aggregation
  - _upsert_covid_batch: idempotent (re-running doesn't duplicate rows)
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from etl.ingest import (
    _parse_covid_row,
    _normalize_dose,
    _safe_int,
    _safe_float,
    _upsert_covid_batch,
)
from etl.economics import (
    _bcb_date_str,
    _bcb_parse_date,
    _sidra_parse_period,
    _date_to_quarter_str,
    aggregate_covid_monthly,
)
from etl.models import CovidCase
from tests.conftest import make_covid_case


# ---------------------------------------------------------------------------
# _parse_covid_row
# ---------------------------------------------------------------------------

class TestParseCovideRow:

    def test_valid_row(self):
        row = {
            "date": "2021-06-15", "state": "pr", "city": "Maringá",
            "city_ibge_code": "4115200", "place_type": "city",
            "confirmed": "5000", "deaths": "100",
            "new_confirmed": "50", "new_deaths": "2",
            "estimated_population": "430000",
            "confirmed_per_100k_inhabitants": "1162.79",
            "death_rate": "0.02", "epidemiological_week": "24",
        }
        result = _parse_covid_row(row)
        assert result is not None
        assert result["state"] == "PR"
        assert result["date"] == date(2021, 6, 15)
        assert result["confirmed"] == 5000
        assert result["deaths"] == 100

    def test_missing_date_returns_none(self):
        assert _parse_covid_row({"state": "PR", "date": ""}) is None

    def test_invalid_date_returns_none(self):
        assert _parse_covid_row({"state": "PR", "date": "not-a-date"}) is None

    def test_none_values_become_none_not_zero(self):
        row = {"date": "2021-01-01", "state": "SP", "city": "",
               "city_ibge_code": "", "place_type": "state",
               "confirmed": "", "deaths": "", "new_confirmed": "",
               "new_deaths": "", "estimated_population": "",
               "confirmed_per_100k_inhabitants": "", "death_rate": ""}
        result = _parse_covid_row(row)
        assert result is not None
        assert result["confirmed"] is None
        assert result["deaths"] is None


# ---------------------------------------------------------------------------
# _normalize_dose
# ---------------------------------------------------------------------------

class TestNormalizeDose:

    @pytest.mark.parametrize("raw,expected", [
        ("1ª Dose", "1"),
        ("Primeira Dose", "1"),
        ("2ª Dose", "2"),
        ("Segunda Dose", "2"),
        ("Reforço", "R"),
        ("Dose Adicional", "R"),
        ("reforço", "R"),
    ])
    def test_dose_normalization(self, raw, expected):
        assert _normalize_dose(raw) == expected


# ---------------------------------------------------------------------------
# Safe type converters
# ---------------------------------------------------------------------------

class TestSafeConverters:

    def test_safe_int_valid(self):
        assert _safe_int("42") == 42
        assert _safe_int(42) == 42

    def test_safe_int_none(self):
        assert _safe_int(None) is None
        assert _safe_int("") is None
        assert _safe_int("None") is None

    def test_safe_int_invalid(self):
        assert _safe_int("abc") is None

    def test_safe_float_valid(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_none(self):
        assert _safe_float("") is None


# ---------------------------------------------------------------------------
# BCB date helpers
# ---------------------------------------------------------------------------

class TestBcbDateHelpers:

    def test_bcb_date_str_format(self):
        assert _bcb_date_str(date(2021, 6, 1)) == "01/06/2021"

    def test_bcb_parse_date_round_trip(self):
        d = date(2021, 6, 1)
        assert _bcb_parse_date(_bcb_date_str(d)) == d


# ---------------------------------------------------------------------------
# SIDRA period parser
# ---------------------------------------------------------------------------

class TestSidraPeriodParser:

    @pytest.mark.parametrize("period,expected", [
        ("202101", date(2021, 1, 1)),
        ("202112", date(2021, 12, 1)),
        ("2021T1", date(2021, 1, 1)),
        ("2021T2", date(2021, 4, 1)),
        ("2021T3", date(2021, 7, 1)),
        ("2021T4", date(2021, 10, 1)),
    ])
    def test_period_parsing(self, period, expected):
        assert _sidra_parse_period(period) == expected

    def test_invalid_period_returns_none(self):
        assert _sidra_parse_period("invalid") is None
        assert _sidra_parse_period("") is None


# ---------------------------------------------------------------------------
# aggregate_covid_monthly
# ---------------------------------------------------------------------------

class TestAggregateCovidMonthly:

    def test_aggregation_sums_by_month(self):
        import pandas as pd
        rows = [
            {"date": "2021-01-05", "cases": 100},
            {"date": "2021-01-15", "cases": 200},
            {"date": "2021-02-10", "cases": 150},
        ]
        import pandas as pd
        df = pd.DataFrame(rows)
        result = aggregate_covid_monthly(df)
        assert len(result) == 2
        jan = result[result["month"] == date(2021, 1, 1)]
        assert int(jan["cases_monthly"].iloc[0]) == 300

    def test_negative_cases_dropped(self):
        import pandas as pd
        df = pd.DataFrame([
            {"date": "2021-01-05", "cases": -10},
            {"date": "2021-01-06", "cases": 100},
        ])
        result = aggregate_covid_monthly(df)
        assert int(result["cases_monthly"].iloc[0]) == 100


# ---------------------------------------------------------------------------
# UPSERT idempotency
# ---------------------------------------------------------------------------

class TestUpsertIdempotency:

    @pytest.mark.asyncio
    async def test_upsert_covid_batch_idempotent(
        self, db_session: AsyncSession
    ):
        batch = [
            {
                "state": "PR", "city": "Maringá",
                "city_ibge_code": "4115200", "place_type": "city",
                "date": date(2021, 6, 1), "confirmed": 100,
                "deaths": 2, "new_confirmed": 10, "new_deaths": 1,
            }
        ]

        await _upsert_covid_batch(db_session, batch)
        await _upsert_covid_batch(db_session, batch)  # second run — must not duplicate

        count = (
            await db_session.execute(
                select(func.count()).select_from(CovidCase)
            )
        ).scalar_one()
        assert count == 1
