"""Чистая логика: расписание, разбор дат и телефонов, тексты."""

from datetime import date, datetime, time

import pytest

from core import schedule, texts
from core.parsing import ParseError, parse_period
from core.phone import normalize_phone

DAY = date(2026, 11, 5)
NOW = datetime(2026, 11, 1, 12, 0)


def hhmm(slots):
    return [s.strftime("%H:%M") for s in slots]


class TestAvailableStarts:
    def test_service_must_end_before_closing(self):
        # 120 мин при работе до 21:00 — последний старт 19:00.
        assert hhmm(schedule.available_starts(DAY, 120, set(), NOW))[-1] == "19:00"

    def test_busy_slot_blocks_overlapping_starts(self):
        busy = {datetime(2026, 11, 5, 12, 0), datetime(2026, 11, 5, 12, 30)}
        starts = hhmm(schedule.available_starts(DAY, 60, busy, NOW))
        assert "11:00" in starts and "13:00" in starts
        assert "11:30" not in starts  # 11:30–12:30 задевает занятый 12:00
        assert "12:00" not in starts and "12:30" not in starts

    def test_lead_time(self):
        now = datetime(2026, 11, 5, 15, 10)
        assert hhmm(schedule.available_starts(DAY, 30, set(), now))[0] == "16:30"

    def test_bookable_range_and_horizon(self):
        assert schedule.bookable_range(NOW, 14) == (date(2026, 11, 1), date(2026, 11, 15))
        assert schedule.is_bookable_day(date(2026, 11, 1), NOW, 0)
        assert not schedule.is_bookable_day(date(2026, 11, 2), NOW, 0)


class TestScheduleHelpers:
    def test_merge_slots(self):
        slots = [datetime(2026, 11, 5, h, m) for h, m in [(10, 0), (10, 30), (11, 0), (14, 0)]]
        merged = [
            (a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in schedule.merge_slots(slots)
        ]
        assert merged == [("10:00", "11:30"), ("14:00", "14:30")]

    def test_day_state(self):
        assert schedule.day_state(DAY, set())[0] == "open"
        assert schedule.day_state(DAY, set(schedule.full_day_slots(DAY)))[0] == "closed"
        state, ranges = schedule.day_state(DAY, set(schedule.window_slots(DAY, time(13), time(14))))
        assert state == "partial" and len(ranges) == 1

    def test_full_day_has_48_slots(self):
        assert len(schedule.full_day_slots(DAY)) == 48


class TestParsePeriod:
    TODAY = date(2026, 11, 1)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("05.11", (date(2026, 11, 5), date(2026, 11, 5), None)),
            ("05.11-10.11", (date(2026, 11, 5), date(2026, 11, 10), None)),
            ("28.12-05.01", (date(2026, 12, 28), date(2027, 1, 5), None)),
            ("05.01", (date(2027, 1, 5), date(2027, 1, 5), None)),  # ближайшая будущая
            ("05.11 14:00-16:00", (date(2026, 11, 5), date(2026, 11, 5), (time(14), time(16)))),
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_period(raw, self.TODAY) == expected

    @pytest.mark.parametrize(
        "raw", ["", "abc", "31.02", "10.11-05.11", "05.11 16:00-14:00", "01.11.2026-05.11.2028"]
    )
    def test_invalid(self, raw):
        with pytest.raises(ParseError):
            parse_period(raw, self.TODAY)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 900 123-45-67", "+79001234567"),
        ("+7 (900) 123 45 67", "+79001234567"),
        ("9001234567", "+79001234567"),
        ("+44 20 7946 0958", "+442079460958"),
        ("12345", None),
        ("abc", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "1 день"), (2, "2 дня"), (5, "5 дней"), (11, "11 дней"), (21, "21 день"), (22, "22 дня")],
)
def test_days_plural(n, expected):
    assert texts.days_ahead(n) == expected
