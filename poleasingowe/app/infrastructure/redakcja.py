"""Zasłanianie poświadczeń w logach i zrzutach (SPEC.md §10.2).

Jedna implementacja dla obu zastosowań, bo spec wymaga, żeby **zrzuty HTML
przechodziły przez ten sam filtr co logi**. Dwie osobne listy wzorców
rozjeżdżają się po pierwszej zmianie i rozjeżdżają się po cichu.

Redakcja jest siatką bezpieczeństwa, nie jedyną obroną. Pierwszą jest to,
że poświadczeń się nie loguje — `Poswiadczenia` i `Ciastko` mają własne
`__repr__`, a `bezpieczny_opis()` istnieje po to, żeby DSN nie trafiał do
logu. Filtr łapie to, czego autor nie przewidział.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MASKA = "***"

# Wzorce celowo WĄSKIE i bez zagnieżdżonych kwantyfikatorów. Zrzut HTML
# potrafi mieć 300 kB, a wzorzec podatny na nawroty zamienia redakcję
# w zawieszenie procesu — tak wywalilo sie kiedys narzedzie do fixtures.
WZORCE: tuple[tuple[re.Pattern[str], str], ...] = (
    # Nagłówki: zasłaniamy wartość, zostawiamy nazwę — po logu ma być widać,
    # ŻE ciasteczko przyszło, bez pokazywania jakie.
    (re.compile(r"(?i)\b(set-cookie\s*:\s*)[^\r\n]{1,4096}"), r"\1" + MASKA),
    (re.compile(r"(?i)\b(cookie\s*:\s*)[^\r\n]{1,4096}"), r"\1" + MASKA),
    (re.compile(r"(?i)\b(authorization\s*:\s*)[^\r\n]{1,4096}"), r"\1" + MASKA),
    # Ukryte pola formularzy: CSRF, XSRF, verification token. Wartość
    # w atrybucie `value`, nazwa pola tuż obok (RECON.md §4.1, §4.4).
    (
        re.compile(
            r'(?i)(name="[^"]{0,120}(?:token|csrf|xsrf)[^"]{0,120}"[^>]{0,200}?'
            r'value=")[^"]{0,4096}'
        ),
        r"\1" + MASKA,
    ),
    # Tokeny i hasła w JSON-ie, w adresach i w kodzie strony.
    (
        re.compile(
            r"(?i)\b((?:xsrf|csrf|access|refresh|api)[-_]?token"
            r"|password|passwd|haslo)"
            r"([\"']?\s*[:=]\s*[\"']?)[^\"'&;\s]{1,4096}"
        ),
        r"\1\2" + MASKA,
    ),
)


class Redakcja:
    """Zasłania poświadczenia w dowolnym tekście.

    `sekrety` to wartości znane z konfiguracji — hasło do bazy i hasła do
    serwisów. Idą osobno od wzorców, bo ich nie da się rozpoznać po
    kształcie: hasło może wyglądać jak zwykłe słowo w zdaniu.
    """

    def __init__(self, sekrety: Iterable[str] = ()) -> None:
        # Od najdłuższego: gdyby jedno hasło było fragmentem drugiego,
        # zasłonięcie krótszego zostawiłoby ogon dłuższego.
        self._sekrety = sorted({s for s in sekrety if s}, key=len, reverse=True)

    def zastosuj(self, tekst: str) -> str:
        for sekret in self._sekrety:
            if sekret in tekst:
                tekst = tekst.replace(sekret, MASKA)
        for wzorzec, zamiennik in WZORCE:
            tekst = wzorzec.sub(zamiennik, tekst)
        return tekst
