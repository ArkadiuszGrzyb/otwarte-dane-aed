from datetime import datetime

import pytest

from mcod.aed.opening_hours import OpeningHoursError, is_open, parse

ALL_DAY = 1440


def week(start, end, days=range(7)):
    return [(day, start, end) for day in days]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("24/7", week(0, ALL_DAY)),
        ("Mo-Su 08:00-20:00", week(480, 1200)),
        ("08:00-20:00", week(480, 1200)),
        ("Mo-Fr 08:00-16:00", week(480, 960, range(5))),
        ("Mo-Fr", week(0, ALL_DAY, range(5))),
        ("Mo,We,Fr 08:00-12:00,13:00-17:00", [(d, s, e) for d in (0, 2, 4) for s, e in ((480, 720), (780, 1020))]),
        ("Sa-Mo 10:00-14:00", [(0, 600, 840), (5, 600, 840), (6, 600, 840)]),
        ("Fr 20:00-02:00", [(4, 1200, ALL_DAY), (5, 0, 120)]),
        ("Su 22:00-26:00", [(0, 0, 120), (6, 1320, ALL_DAY)]),
        ("Mo-Su 00:00-24:00", week(0, ALL_DAY)),
        ("Mo-Su 08:00-20:00; Su off", week(480, 1200, range(6))),
        ("Mo-Fr 08:00-16:00; We 10:00-12:00", [(0, 480, 960), (1, 480, 960), (2, 600, 720), (3, 480, 960), (4, 480, 960)]),
        ("Mo-Fr 08:00-12:00, We 14:00-16:00", [(0, 480, 720), (1, 480, 720), (2, 480, 720), (2, 840, 960), (3, 480, 720), (4, 480, 720)]),
        ("Mo 08:00-12:00,10:00-14:00", [(0, 480, 840)]),
        ("mo-fr 8:00-16:00", week(480, 960, range(5))),
        ("off", []),
    ],
)
def test_parse(text, expected):
    assert parse(text) == sorted(expected)


@pytest.mark.parametrize(
    "text",
    ["", "Mo-Fr 08:00", "Mo-Fr 25:00-26:00", "Mo 08:00-08:00", "Mo 08:61-09:00", "PH off",
     "Jan-Mar 08:00-16:00", "Mo-Fr 08:00+", "Mo-Fr 08:00-16:00 || Sa 10:00-12:00", "sunrise-sunset",
     "Mo-Fr 08:00-16:00 \"call ahead\"", "Mo-Fr 08:00-16:00,", "Xy 08:00-10:00"],
)
def test_parse_rejects_invalid_or_unsupported(text):
    with pytest.raises(OpeningHoursError):
        parse(text)


MONDAY_10 = datetime(2026, 11, 23, 10, 0)
MONDAY_21 = datetime(2026, 11, 23, 21, 0)
SATURDAY_01 = datetime(2026, 11, 28, 1, 0)


@pytest.mark.parametrize(
    "text, moment, expected",
    [
        # valid text: filter applies
        ("Mo-Fr 08:00-16:00", MONDAY_10, True),
        ("Mo-Fr 08:00-16:00", MONDAY_21, False),
        ("Mo-Fr 08:00-16:00", datetime(2026, 11, 23, 16, 0), False),  # end is exclusive
        ("Mo-Fr 08:00-16:00", SATURDAY_01, False),
        ("Fr 20:00-02:00", SATURDAY_01, True),
        ("off", MONDAY_10, False),
        # anything else: no filter, always open
        (None, MONDAY_21, True),
        ("", MONDAY_21, True),
        ("   ", MONDAY_21, True),
        ("pn-nd 08:00-20:00", MONDAY_21, True),
        ("PH off", MONDAY_21, True),
        ("całodobowo", MONDAY_21, True),
    ],
)
def test_is_open(text, moment, expected):
    assert is_open(text, moment) is expected
