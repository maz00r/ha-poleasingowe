"""Enumeracje domenowe.

SPEC.md §6.3 zabrania magicznych stringów. SPEC.md §8.2 zabrania typów enum
PostgreSQL („bo ich migracje bolą") — w bazie są to `text` z `CHECK`, a lista
dozwolonych wartości pochodzi stąd. Migracja generuje `CHECK` z tych wartości,
żeby nie rozjechały się dwa źródła prawdy.
"""

from __future__ import annotations

from enum import StrEnum


class AuctionStatus(StrEnum):
    """Stan aukcji widziany przez nas, nie przez serwis."""

    ACTIVE = "ACTIVE"
    ENDING = "ENDING"
    """Po `ends_at`, w trakcie domykania (SPEC.md §11.5)."""
    ENDED = "ENDED"
    DISAPPEARED = "DISAPPEARED"
    """Zniknęła z serwisu bez pokazania ceny końcowej."""


class FinalPriceState(StrEnum):
    """Jakość pomiaru ceny końcowej (SPEC.md §11.5).

    Rozróżnienie jest kluczowe dla `v_market_stats` (§9): mediana licząca
    `LAST_SEEN` jako pewne to zafałszowany obraz rynku.
    """

    UNKNOWN = "UNKNOWN"
    """Aukcja jeszcze trwa albo domykanie się nie zaczęło."""
    CONFIRMED = "CONFIRMED"
    """Cena odczytana ze strony PO zakończeniu."""
    LAST_SEEN = "LAST_SEEN"
    """Ostatnia obserwacja przed zamknięciem. Dolne oszacowanie."""


class PollTier(StrEnum):
    """Kubełek harmonogramu (SPEC.md §11.2). Interwały są w `PollingPolicy`."""

    IDLE = "IDLE"
    """Nieobserwowana — nie odpytywana pojedynczo w ogóle."""
    FAR = "FAR"
    NEAR = "NEAR"
    ENDGAME = "ENDGAME"
    CLOSING = "CLOSING"
    """Po `ends_at`, faza czujki dogrywki lub drabinki (SPEC.md §11.5)."""


class AuthState(StrEnum):
    """Stan uwierzytelnienia źródła (SPEC.md §10.2)."""

    ANONYMOUS = "ANONYMOUS"
    """Źródło nie wymaga logowania do odczytu."""
    OK = "OK"
    EXPIRED = "EXPIRED"
    LOCKED = "LOCKED"
    """Przekroczony twardy limit 3 nieudanych logowań. Wymaga resetu z UI."""


class Currency(StrEnum):
    """Waluty, w których serwisy podają kwoty (RECON.md §4.2)."""

    PLN = "PLN"
    EUR = "EUR"


class BidCountSemantics(StrEnum):
    """Co serwis liczy w `bid_count` (SPEC.md §11.8, RECON.md §3.5).

    Od tego zależy, czy `bid_gap` w ogóle coś znaczy. EFL prowadzi licytację
    proxy i pokazuje jeden wiersz na uczestnika, aktualizowany w miejscu —
    cena rośnie przy niezmienionym `bid_count`, więc przyrost nie mierzy
    przegapionych ofert.
    """

    OFFERS = "OFFERS"
    """Licznik rośnie o jeden na ofertę — `bid_gap` jest policzalny."""
    PARTICIPANTS = "PARTICIPANTS"
    """Licznik zlicza uczestników licytacji proxy — `bid_gap` zostaje `NULL`."""
    UNKNOWN = "UNKNOWN"
    """Rekonesans nie dał dowodu. Domyślne: niewiedza, nie założenie."""


class RodzajPojazdu(StrEnum):
    """Czym jest pojazd (SPEC.md §8.2, §12).

    Serwisy sprzedają w jednej kategorii samochody, ciągniki siodłowe,
    furgony i naczepy — bez tego rozróżnienia lista aukcji miesza je ze sobą
    i filtr „marka" nie pomaga, bo naczepa też ma markę.

    Klasyfikacja idzie przez warstwę antykorupcyjną, bo każde źródło mówi
    o tym samym inaczej: autoprzetarg kategorią w adresie, EFL polem
    `Rodzaj pojazdu`, poleasingowe wyłącznie nazwą nadwozia w tytule.
    """

    OSOBOWY = "OSOBOWY"
    DOSTAWCZY = "DOSTAWCZY"
    """Do 3,5 t: furgon, blaszak, plandeka, brygadówka."""
    CIEZAROWY = "CIEZAROWY"
    """Powyżej 3,5 t, razem z ciągnikami siodłowymi."""
    MOTOCYKL = "MOTOCYKL"
    PRZYCZEPA = "PRZYCZEPA"
    """Przyczepy i naczepy — bez własnego napędu."""
    AUTOBUS = "AUTOBUS"
    INNY = "INNY"
    """Rozpoznane, ale poza powyższymi: maszyny, quady, kampery."""
    NIEZNANY = "NIEZNANY"
    """Źródło nic nie powiedziało i nazwa nic nie zdradza.

    Osobna wartość od `INNY`: „nie wiem" to nie to samo co „wiem, że inny",
    a filtr domyślny nie ma prawa ukrywać pojazdów tylko dlatego, że nie
    umieliśmy ich nazwać.
    """
