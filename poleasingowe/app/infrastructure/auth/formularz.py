"""Odczyt formularza logowania ze strony (SPEC.md §10.2).

„Tokeny CSRF i ukryte pola formularza wyciągane ze strony logowania przy
każdej próbie. Nic zaszytego na sztywno." Fixtures pokazują, dlaczego to
nie jest przesada:

- **autoprzetarg.pl** ma na stronie logowania **dwa** pola
  `__RequestVerificationToken` — jedno w formularzu wyszukiwarki, drugie
  w formularzu logowania, każde z inną wartością. Wzięcie „pierwszego
  tokenu na stronie" daje token od wyszukiwarki i odrzucone logowanie.
- **aukcje.leasygroup.pl** ma ukryte pole, w którym losowa jest **nazwa,
  nie tylko wartość** (`Tzd5UWsvaU9kQlMzbWhuNzIwMk9TQT09`). Nie da się go
  wymienić z nazwy — trzeba przepisać wszystko, co formularz niesie.

Stąd reguła: znajdź formularz **po polu hasła**, przepisz z niego wszystkie
pola, podmień login i hasło. Nazw pól nie zgadujemy z niczego poza tym,
co stoi w HTML-u.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from app.domain.errors import ParseFailed

# `type` bywa zapisany dowolną wielkością liter — autoprzetarg ma
# `type="Password"` z wielkiej litery i porównanie wprost go gubi.
TYP_HASLA = "password"

# Pola, których NIE przepisujemy z formularza: przyciski wysyłają wartość
# tylko wtedy, gdy się je kliknie, a `file` nie ma tu czego wnieść.
POMIJANE_TYPY = frozenset({"submit", "button", "image", "reset", "file"})


@dataclass(slots=True, frozen=True)
class FormularzLogowania:
    """Gotowy do wysłania formularz: dokąd, czym i z jakimi polami."""

    akcja: str
    """Adres bezwzględny — `action` bywa względny albo pusty."""
    metoda: str
    pola: dict[str, str] = field(default_factory=dict)
    pole_loginu: str = ""
    pole_hasla: str = ""

    def z_poswiadczeniami(self, login: str, haslo: str) -> dict[str, str]:
        """Pola do wysłania. **Nie loguj tego** — zawiera hasło (§10.2)."""
        dane = dict(self.pola)
        if self.pole_loginu:
            dane[self.pole_loginu] = login
        dane[self.pole_hasla] = haslo
        return dane

    def __repr__(self) -> str:
        # Redakcja u źródła: sam formularz hasła jeszcze nie ma, ale ukryte
        # pola bywają tokenami sesji i nie mają po co trafiać do logu.
        return (
            f"FormularzLogowania(akcja={self.akcja!r}, metoda={self.metoda!r}, "
            f"pola={sorted(self.pola)!r}, pole_loginu={self.pole_loginu!r})"
        )

    __str__ = __repr__


def _typ(wezel: Node) -> str:
    return (wezel.attributes.get("type") or "text").strip().lower()


def _formularz_z_haslem(drzewo: HTMLParser) -> Node:
    for formularz in drzewo.css("form"):
        if any(_typ(pole) == TYP_HASLA for pole in formularz.css("input")):
            return formularz
    raise ParseFailed(
        "na stronie logowania nie ma formularza z polem hasła — "
        "serwis zmienił układ albo strona wymaga JavaScriptu"
    )


def wczytaj_formularz(html: str, url_strony: str) -> FormularzLogowania:
    """Wyciąga formularz logowania z HTML-a strony logowania.

    `url_strony` służy do rozwinięcia względnego `action`; pusty `action`
    znaczy „wyślij pod ten sam adres", co jest zgodne z HTML-em i zdarza się
    w praktyce.
    """
    formularz = _formularz_z_haslem(HTMLParser(html))

    pola: dict[str, str] = {}
    pole_hasla = ""
    pole_loginu = ""
    for wejscie in formularz.css("input"):
        nazwa = wejscie.attributes.get("name")
        typ = _typ(wejscie)
        if not nazwa or typ in POMIJANE_TYPY:
            continue
        if typ == TYP_HASLA:
            pole_hasla = nazwa
            pola[nazwa] = ""
            continue
        # Niezaznaczony checkbox nie jest wysylany. EFL ma parę
        # `RememberMe=true` (checkbox) i `RememberMe=false` (hidden) —
        # pominięcie checkboxa zostawia poprawne `false`.
        #
        # Obecność sprawdzamy przez `in`, nie przez wartość: `checked` to
        # atrybut logiczny, więc dla `<input checked>` selectolax zwraca
        # `None` — dokładnie to samo, co dla atrybutu nieobecnego.
        if typ == "checkbox" and "checked" not in wejscie.attributes:
            continue
        if not pole_loginu and typ in {"text", "email", "tel"}:
            pole_loginu = nazwa
        pola.setdefault(nazwa, wejscie.attributes.get("value") or "")

    if not pole_hasla:  # pragma: no cover — `_formularz_z_haslem` to gwarantuje
        raise ParseFailed("formularz nie ma pola hasła")

    akcja = (formularz.attributes.get("action") or "").strip()
    metoda = (formularz.attributes.get("method") or "post").strip().lower()
    return FormularzLogowania(
        akcja=urljoin(url_strony, akcja) if akcja else url_strony,
        metoda=metoda,
        pola=pola,
        pole_loginu=pole_loginu,
        pole_hasla=pole_hasla,
    )
