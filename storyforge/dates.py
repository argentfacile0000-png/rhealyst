"""Lecture de dates en français pour programmer une publication."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
MONTHS = {
    "janvier": 1, "janv": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4, "mai": 5,
    "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
}

EXAMPLES = "demain 18h · lundi 9h30 · 25/12 18:30 · 25 décembre 20h · 2026-12-25 18:30 · dans 2h"


def _plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.replace(",", " ")).strip()


def _parse_time(t: str) -> tuple[time | None, str]:
    """Extrait une heure (18h, 18h30, 18:30, 9 h) et renvoie le texte restant."""
    m = re.search(r"\b(?:a\s+)?([01]?\d|2[0-3])\s*(?:h|:)\s*([0-5]\d)?\b", t)
    if not m:
        return None, t
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    return time(hour, minute), (t[: m.start()] + " " + t[m.end():]).strip()


def parse_when(text: str, tz: str = "Europe/Paris", now: datetime | None = None) -> datetime:
    """Texte → date/heure (fuseau de la chaîne). Lève ValueError si incompréhensible ou passée."""
    zone = ZoneInfo(tz)
    now = (now or datetime.now(zone)).astimezone(zone)
    t = _plain(text)
    if not t:
        raise ValueError("Date vide")

    m = re.fullmatch(r"dans\s+(\d+)\s*(min|minutes?|h|heures?|j|jours?)", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        delta = timedelta(minutes=n) if unit == "m" else timedelta(hours=n) if unit == "h" else timedelta(days=n)
        return _check(now + delta, now)

    when_time, rest = _parse_time(t)
    rest = re.sub(r"\b(le|a|à|pour)\b", " ", rest).strip()
    day: date | None = None

    if re.fullmatch(r"(aujourd'?hui|auj|ce soir)?", rest):
        day = now.date()
    elif rest in ("demain",):
        day = now.date() + timedelta(days=1)
    elif rest in ("apres-demain", "apres demain"):
        day = now.date() + timedelta(days=2)
    elif rest in WEEKDAYS:
        ahead = (WEEKDAYS[rest] - now.weekday()) % 7
        day = now.date() + timedelta(days=ahead or 7)
    else:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", rest)
        if m:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = m or re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", rest)
        if m and day is None:
            d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
            year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else now.year
            day = date(year, mo, d)
            if not y and day < now.date():
                day = date(year + 1, mo, d)
        if day is None:
            m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\.?(?:\s+(\d{4}))?", rest)
            if m and m.group(2) in MONTHS:
                year = int(m.group(3)) if m.group(3) else now.year
                day = date(year, MONTHS[m.group(2)], int(m.group(1)))
                if not m.group(3) and day < now.date():
                    day = date(year + 1, MONTHS[m.group(2)], int(m.group(1)))
    if day is None:
        raise ValueError(f"Date non reconnue. Exemples : {EXAMPLES}")
    if when_time is None:
        raise ValueError("Précise l'heure, par exemple « demain 18h »")
    result = datetime.combine(day, when_time, tzinfo=zone)
    if result <= now and not rest:  # « 18h » déjà passé aujourd'hui : demain
        result += timedelta(days=1)
    return _check(result, now)


def _check(result: datetime, now: datetime) -> datetime:
    if result <= now + timedelta(minutes=1):
        raise ValueError("Cette date est déjà passée")
    if result > now + timedelta(days=366):
        raise ValueError("Programme au maximum un an à l'avance")
    return result
