"""Parser for the weekly subset of the OSM ``opening_hours`` syntax.

https://wiki.openstreetmap.org/wiki/Key:opening_hours/specification

Supported:
    24/7
    Mo-Fr 08:00-16:00
    Mo,We,Fr 08:00-12:00,13:00-17:00
    Sa-Mo 10:00-14:00            (wrapping weekday range)
    Fr 20:00-02:00               (past midnight, spills into the next day)
    Mo-Fr 18:00-26:00            (extended hours, same as above)
    Mo-Fr                        (weekdays only = open all day)
    08:00-20:00                  (no weekdays = every day)
    Mo-Su 08:00-20:00; Su off    (";" = later rule overrides the days it names)
    Mo-Fr 08:00-12:00, We 14:00-16:00   ("," + weekday = additional rule)

Not supported (raise OpeningHoursError, so is_open treats them as always open): public/school
holidays (PH, SH), months and dates, week numbers, sunrise/sunset, open-ended
"+", "||" fallback rules and comments.

Use ``is_open(text, moment)``: text that is empty or not valid supported syntax
(e.g. Polish "pn-nd 08:00-20:00") imposes no restriction and counts as always open.
"""
import re

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]  # index == datetime.weekday()
MINUTES_PER_DAY = 24 * 60

ERROR_MESSAGE = "Niepoprawny zapis godzin dostępu"

_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<always>24/7)"
    r"|(?P<time>\d{1,2}:\d{2})"
    r"|(?P<day>Mo|Tu|We|Th|Fr|Sa|Su)(?![a-z])"
    r"|(?P<off>off|closed)(?![a-z])"
    r"|(?P<sym>[-,;])"
    r")",
    re.IGNORECASE,
)


class OpeningHoursError(ValueError):
    pass


def is_open(text, moment):
    """True if ``moment`` (a local datetime) falls within ``text``.

    Empty or invalid/unsupported text means no restriction, so it is always open.
    """
    if not text or not text.strip():
        return True
    try:
        slots = parse(text)
    except OpeningHoursError:
        return True
    weekday = moment.weekday()
    minute = moment.hour * 60 + moment.minute
    return any(day == weekday and start <= minute < end for day, start, end in slots)


def parse(text):
    """Return weekly slots as a sorted list of ``(weekday, start_minute, end_minute)``.

    ``weekday`` is 0 (Mo) .. 6 (Su); minutes are 0..1440 and ``start < end``.
    Spans past midnight are split between both days; overlapping spans are merged.
    Raises ``OpeningHoursError`` when the text is not valid supported syntax.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise OpeningHoursError("%s: pusty zapis" % ERROR_MESSAGE)

    # Spans keyed by the day of the rule that produced them (end may exceed 1440 = spill).
    by_origin = {day: [] for day in range(7)}
    pos = 0
    additive = False
    while True:
        days, spans, pos = _parse_rule(tokens, pos)
        for day in days:
            if not additive or spans is None:
                by_origin[day] = []
            if spans:
                by_origin[day].extend(spans)

        if pos == len(tokens):
            break
        kind, value = tokens[pos]
        if value == ";":
            additive = False
        elif value == "," and pos + 1 < len(tokens) and tokens[pos + 1][0] == "day":
            additive = True
        else:
            raise OpeningHoursError("%s: nieoczekiwane '%s'" % (ERROR_MESSAGE, value))
        pos += 1

    by_day = {day: [] for day in range(7)}
    for day, spans in by_origin.items():
        for start, end in spans:
            by_day[day].append((start, min(end, MINUTES_PER_DAY)))
            if end > MINUTES_PER_DAY:
                by_day[(day + 1) % 7].append((0, end - MINUTES_PER_DAY))

    slots = []
    for day in range(7):
        for start, end in _merge(by_day[day]):
            slots.append((day, start, end))
    return slots


def _tokenize(text):
    tokens = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if not match:
            raise OpeningHoursError(
                "%s: nieobsługiwany fragment '%s'" % (ERROR_MESSAGE, text[pos:].strip()[:30])
            )
        kind = match.lastgroup
        value = match.group(kind)
        if kind == "day":
            value = DAYS.index(value.capitalize())
        elif kind == "time":
            value = _to_minutes(value)
        tokens.append((kind, value))
        pos = match.end()
    return tokens


def _to_minutes(value):
    hours, minutes = (int(part) for part in value.split(":"))
    if minutes > 59 or hours > 48 or (hours == 48 and minutes):
        raise OpeningHoursError("%s: niepoprawna godzina '%s'" % (ERROR_MESSAGE, value))
    return hours * 60 + minutes


def _parse_rule(tokens, pos):
    """rule := "24/7" | [days] ("off" | spans)?   ->  (days, spans or None for off, pos)"""
    if tokens[pos][0] == "always":
        return list(range(7)), [(0, MINUTES_PER_DAY)], pos + 1

    days, pos = _parse_days(tokens, pos)
    kind = tokens[pos][0] if pos < len(tokens) else None
    if kind == "off":
        return days or list(range(7)), None, pos + 1
    if kind == "time":
        spans, pos = _parse_spans(tokens, pos)
        return days or list(range(7)), spans, pos
    if days:
        return days, [(0, MINUTES_PER_DAY)], pos
    found = tokens[pos][1] if pos < len(tokens) else "koniec"
    raise OpeningHoursError("%s: oczekiwano dni lub godzin, jest '%s'" % (ERROR_MESSAGE, found))


def _parse_days(tokens, pos):
    days = []
    while pos < len(tokens) and tokens[pos][0] == "day":
        first = tokens[pos][1]
        pos += 1
        if _is(tokens, pos, "-") and pos + 1 < len(tokens) and tokens[pos + 1][0] == "day":
            last = tokens[pos + 1][1]
            pos += 2
            days.extend((first + offset) % 7 for offset in range((last - first) % 7 + 1))
        else:
            days.append(first)
        # "," continues the weekday list only when another weekday follows.
        if _is(tokens, pos, ",") and pos + 1 < len(tokens) and tokens[pos + 1][0] == "day":
            pos += 1
        else:
            break
    return sorted(set(days)), pos


def _parse_spans(tokens, pos):
    spans = []
    while True:
        if not (
            pos + 2 < len(tokens)
            and tokens[pos][0] == "time"
            and _is(tokens, pos + 1, "-")
            and tokens[pos + 2][0] == "time"
        ):
            raise OpeningHoursError("%s: oczekiwano przedziału godzin HH:MM-HH:MM" % ERROR_MESSAGE)
        start, end = tokens[pos][1], tokens[pos + 2][1]
        pos += 3
        if start >= MINUTES_PER_DAY:
            raise OpeningHoursError("%s: godzina początkowa musi być przed 24:00" % ERROR_MESSAGE)
        if end == start:
            raise OpeningHoursError("%s: pusty przedział godzin" % ERROR_MESSAGE)
        if end < start:
            end += MINUTES_PER_DAY  # e.g. 20:00-02:00 ends the next day
        spans.append((start, end))
        if _is(tokens, pos, ",") and pos + 1 < len(tokens) and tokens[pos + 1][0] == "time":
            pos += 1
        else:
            return spans, pos


def _is(tokens, pos, symbol):
    return pos < len(tokens) and tokens[pos] == ("sym", symbol)


def _merge(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
