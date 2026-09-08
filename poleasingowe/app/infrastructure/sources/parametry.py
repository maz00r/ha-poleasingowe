"""Parametry źródeł z rekonesansu (SPEC.md §8.1, §11.2, §11.5).

Okno dogrywki, siatka domknięcia i semantyka `bid_count` są **kolumnami
w `source`**, nie stałymi w kodzie — cztery zbadane serwisy mają cztery różne
reguły. Ale skądś te kolumny muszą dostać wartość początkową, a opcje add-onu
ich nie zawierają: użytkownik nie ma skąd wiedzieć, że poleasingowe.pl
przedłuża aukcję o 30 s, a autoprzetarg o 120 s.

Stąd ten moduł: **wartości zmierzone w rekonesansie**, wpisywane przy
rejestracji źródła. To nie jest konfiguracja — to zapis faktów o serwisach,
z podaniem miejsca w `RECON.md`, gdzie stoi dowód.

Opcje add-onu nadpisują z tego wyłącznie to, co użytkownik faktycznie
ustawia: włączenie źródła, limit tempa i floor.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities import Source
from app.domain.enums import AuthState, BidCountSemantics


@dataclass(slots=True, frozen=True)
class ParametryZrodla:
    """Zmierzone zachowanie serwisu — nie preferencje użytkownika."""

    nazwa: str
    overtime_window_seconds: int
    overtime_extension_seconds: int
    overtime_cap_seconds: int | None
    closing_ladder_seconds: tuple[int, ...]
    bid_history_ttl_seconds: int | None
    bid_count_semantics: BidCountSemantics
    wymaga_logowania: bool
    dowod: str


ZNANE: dict[str, ParametryZrodla] = {
    "efl": ParametryZrodla(
        nazwa="EFL",
        # Brak dogrywki — `ends_at` jest twardy (RECON.md §3.2).
        overtime_window_seconds=0,
        overtime_extension_seconds=0,
        overtime_cap_seconds=None,
        # Strona po zakończeniu zamraża się i trzyma cenę ≥2 h (RECON.md §3.6).
        closing_ladder_seconds=(2, 30),
        bid_history_ttl_seconds=None,
        # Licytacja proxy: jeden wiersz na UCZESTNIKA, nie na ofertę
        # (RECON.md §3.5) — `bid_gap` z §11.8 zostaje tu `NULL`.
        bid_count_semantics=BidCountSemantics.PARTICIPANTS,
        wymaga_logowania=False,
        dowod="RECON.md §4.1, §3.5, §3.6",
    ),
    "poleasingowe": ParametryZrodla(
        nazwa="poleasingowe.pl",
        overtime_window_seconds=30,
        overtime_extension_seconds=30,
        overtime_cap_seconds=1800,
        closing_ladder_seconds=(2, 30),
        # `lastOffers` czyszczone 2-5 min po końcu (RECON.md §3.6).
        bid_history_ttl_seconds=120,
        bid_count_semantics=BidCountSemantics.OFFERS,
        wymaga_logowania=False,
        dowod="RECON.md §4.2, §3.6, §3.7",
    ),
    "autoprzetarg": ParametryZrodla(
        nazwa="autoprzetarg.pl",
        overtime_window_seconds=120,
        overtime_extension_seconds=120,
        overtime_cap_seconds=None,
        # Cena znika 10-15 s po końcu (302 na `/`), więc ostatnie stopnie
        # domyślnej siatki trafiałyby już w przekierowanie (RECON.md §3.4).
        closing_ladder_seconds=(2, 5, 8, 11, 14),
        bid_history_ttl_seconds=15,
        bid_count_semantics=BidCountSemantics.UNKNOWN,
        # Odczyt DZIAŁA bez konta: lista niesie komplet danych technicznych
        # z VIN-em i terminem. Sesja dokłada wyłącznie liczbę i historię ofert
        # (RECON.md §4.4), więc źródło startuje jako ANONYMOUS — `EXPIRED`
        # znaczyłoby „trzeba się zalogować", a to nieprawda dla odczytu.
        wymaga_logowania=False,
        dowod="RECON.md §4.4, §3.4, §3.6",
    ),
    "leasygroup": ParametryZrodla(
        nazwa="aukcje.leasygroup.pl",
        overtime_window_seconds=120,
        overtime_extension_seconds=120,
        overtime_cap_seconds=None,
        # Niezmierzone — siatka domyślna, do kalibracji (RECON.md §3.6).
        closing_ladder_seconds=(2, 5, 10, 20, 40),
        bid_history_ttl_seconds=None,
        bid_count_semantics=BidCountSemantics.UNKNOWN,
        wymaga_logowania=False,
        dowod="RECON.md §4.3",
    ),
}


def zbuduj_source(
    key: str,
    *,
    enabled: bool,
    rate_limit_per_minute: int,
    floor_seconds: int,
) -> Source:
    """Składa wiersz `source` z faktów o serwisie i ustawień użytkownika.

    Nieznany klucz dostaje wartości neutralne: zero dogrywki i `UNKNOWN`.
    „Nie wiem" jest tu właściwą odpowiedzią — zgadnięte okno dogrywki
    przełożyłoby się wprost na przegapioną cenę końcową.
    """
    znane = ZNANE.get(key)
    if znane is None:
        return Source(
            key=key,
            name=key,
            enabled=enabled,
            sweep_interval_seconds=21_600,
            rate_limit_per_minute=rate_limit_per_minute,
            floor_seconds=floor_seconds,
            auth_state=AuthState.ANONYMOUS,
            consecutive_auth_failures=0,
            overtime_window_seconds=0,
            overtime_extension_seconds=0,
            overtime_cap_seconds=None,
        )

    return Source(
        key=key,
        name=znane.nazwa,
        enabled=enabled,
        sweep_interval_seconds=21_600,
        rate_limit_per_minute=rate_limit_per_minute,
        floor_seconds=floor_seconds,
        # `EXPIRED` znaczy „trzeba się zalogować", `ANONYMOUS` — „to źródło
        # czyta się bez konta" (SPEC.md §10.2). To nie to samo.
        auth_state=AuthState.EXPIRED if znane.wymaga_logowania else AuthState.ANONYMOUS,
        consecutive_auth_failures=0,
        overtime_window_seconds=znane.overtime_window_seconds,
        overtime_extension_seconds=znane.overtime_extension_seconds,
        overtime_cap_seconds=znane.overtime_cap_seconds,
        closing_ladder_seconds=znane.closing_ladder_seconds,
        bid_history_ttl_seconds=znane.bid_history_ttl_seconds,
        bid_count_semantics=znane.bid_count_semantics,
    )
