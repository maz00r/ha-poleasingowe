"""Value objects z walidacją w konstruktorze (SPEC.md §6.2).

**Nigdy `float` do ceny.** Kwoty to `Decimal`, w bazie `numeric(12,2)`
plus kolumna waluty (§8.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.enums import Currency

# Kwoty w bazie to numeric(12,2): 10 cyfr przed przecinkiem, 2 po.
_MAKS_KWOTA = Decimal("9999999999.99")
_GROSZ = Decimal("0.01")

# VIN: 17 znaków, bez I, O i Q — te są wykluczone normą, żeby nie mylić
# się z 1 i 0. Serwisy bywają niechlujne, więc walidujemy, zamiast ufać.
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class NieprawidlowaWartosc(ValueError):
    """Value object dostał wartość, której nie da się sensownie zinterpretować."""


@dataclass(slots=True, frozen=True)
class Money:
    """Kwota z walutą. Zaokrąglana do grosza już w konstruktorze."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise NieprawidlowaWartosc(
                f"kwota musi być Decimal, dostałem {type(self.amount).__name__} "
                "— float nie wchodzi w grę (SPEC.md §6.2)"
            )
        if self.amount.is_nan() or self.amount.is_infinite():
            raise NieprawidlowaWartosc(
                f"kwota nie jest liczbą skończoną: {self.amount}"
            )
        if self.amount < 0:
            raise NieprawidlowaWartosc(f"kwota ujemna: {self.amount}")
        if self.amount > _MAKS_KWOTA:
            raise NieprawidlowaWartosc(
                f"kwota {self.amount} nie mieści się w numeric(12,2) (SPEC.md §8.2)"
            )
        object.__setattr__(self, "amount", self.amount.quantize(_GROSZ))

    @classmethod
    def z_tekstu(cls, tekst: str, currency: Currency) -> Money:
        """Parsuje kwotę w formatach, w jakich podają ją serwisy.

        Obsługuje spacje i twarde spacje jako separator tysięcy oraz przecinek
        jako separator dziesiętny: `48 600,00`, `110 600`, `11579,31`
        (RECON.md §4.1, §4.2, §4.4).
        """
        oczyszczony = tekst.strip().replace("\xa0", "").replace(" ", "")
        oczyszczony = oczyszczony.replace("zł", "").replace("PLN", "").strip()
        oczyszczony = oczyszczony.replace(",", ".")
        if not oczyszczony:
            raise NieprawidlowaWartosc(f"pusta kwota: {tekst!r}")
        try:
            return cls(Decimal(oczyszczony), currency)
        except InvalidOperation as exc:
            raise NieprawidlowaWartosc(f"nie umiem odczytać kwoty: {tekst!r}") from exc

    def __str__(self) -> str:
        return f"{self.amount} {self.currency.value}"


@dataclass(slots=True, frozen=True)
class Mileage:
    """Przebieg w kilometrach."""

    km: int

    def __post_init__(self) -> None:
        if not isinstance(self.km, int) or isinstance(self.km, bool):
            raise NieprawidlowaWartosc(f"przebieg musi być int, dostałem {self.km!r}")
        if self.km < 0:
            raise NieprawidlowaWartosc(f"przebieg ujemny: {self.km}")
        # Rekord swiata to ~5 mln km. Wyzsza wartosc to blad parsowania,
        # a nie samochod — lepiej odrzucic niz zapisac smiec.
        if self.km > 5_000_000:
            raise NieprawidlowaWartosc(f"przebieg nierealny: {self.km} km")

    @classmethod
    def z_tekstu(cls, tekst: str) -> Mileage:
        """`180848km`, `216 146 km`, `128 541 km` (RECON.md §4.1, §4.3)."""
        cyfry = re.sub(r"[^\d]", "", tekst)
        if not cyfry:
            raise NieprawidlowaWartosc(f"brak cyfr w przebiegu: {tekst!r}")
        return cls(int(cyfry))

    def __str__(self) -> str:
        return f"{self.km} km"


@dataclass(slots=True, frozen=True)
class Vin:
    """Numer VIN. Normalizowany do wielkich liter.

    Podstawa deduplikacji między serwisami (SPEC.md §8.4), więc niechlujny
    VIN jest gorszy niż jego brak — lepiej odrzucić i zostawić `NULL`.
    """

    value: str

    def __post_init__(self) -> None:
        znormalizowany = self.value.strip().upper()
        if not _VIN.match(znormalizowany):
            raise NieprawidlowaWartosc(
                f"VIN musi mieć 17 znaków bez I, O i Q — dostałem {self.value!r}"
            )
        object.__setattr__(self, "value", znormalizowany)

    def __str__(self) -> str:
        return self.value
