"""Checks for live_feed.next_check — run: python test_live_feed.py"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from live_feed import LIVE_SECONDS, POLL_SECONDS, next_check, tournament_day
from tournaments import Tournament

TZ = ZoneInfo("Europe/Berlin")


def at(hour: int, minute: int = 0, day: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def match(status: str, time_note: str = "Followed by", category: str = "Women", court: str = "CENTER") -> dict:
    return {"court": court, "category": category, "status": status, "time_note": time_note}


def test_before_the_first_match_wakes_ten_minutes_early():
    day = [match("upcoming", "Starting at 10:00 AM"), match("upcoming")]
    assert next_check(day, at(8)) == 2 * 3600 - 600


def test_followed_by_after_a_finished_match_is_polled():
    day = [match("completed", "Starting at 9:00 AM"), match("upcoming")]
    assert next_check(day, at(11)) == POLL_SECONDS


def test_not_before_waits_until_that_time():
    day = [match("completed", "Starting at 9:00 AM"), match("upcoming", "Not before 3:00 PM")]
    assert next_check(day, at(12)) == 3 * 3600
    assert next_check(day, at(15, 20)) == POLL_SECONDS   # the time has come, not started yet


def test_24_hour_times():
    assert next_check([match("upcoming", "Not before 14:30")], at(14)) == 1800


def test_live_match_wins():
    day = [match("in_progress"), match("upcoming", "Not before 5:00 PM", court="COURT 2")]
    assert next_check(day, at(12)) == LIVE_SECONDS


def test_women_wait_for_the_men_ahead_on_the_court():
    men_live = [match("in_progress", category="Men"), match("upcoming")]
    assert next_check(men_live, at(12)) == POLL_SECONDS
    men_later = [match("upcoming", "Not before 4:00 PM", category="Men"), match("upcoming")]
    assert next_check(men_later, at(15)) == 3600


def test_day_is_over_when_the_women_are_done():
    day = [match("completed"), match("in_progress", category="Men"), match("upcoming", category="Men")]
    assert next_check(day, at(18)) is None


def test_night_session_belongs_to_the_day_before():
    germany = Tournament("germany-p2-2026", "Germany P2", "P2", 1, 2026, date(2026, 10, 4), 8, None, "Europe/Berlin")
    assert tournament_day(germany, at(23, 30, day=6)) == 3
    assert tournament_day(germany, at(1, 30, day=7)) == 3
    assert tournament_day(germany, at(9, day=7)) == 4
    assert tournament_day(germany, at(12, day=3)) is None
    assert tournament_day(germany, at(12, day=12)) is None
    late = [match("upcoming", "Not before 11:00 PM")]
    assert next_check(late, at(0, 30, day=7)) == POLL_SECONDS   # 11 PM of the day being played


if __name__ == "__main__":
    for name, check in list(globals().items()):
        if name.startswith("test_"):
            check()
    print("ok")
