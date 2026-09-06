"""Warstwa domenowa — encje, value objects i reguły.

**Zero zależności zewnętrznych.** Wyłącznie biblioteka standardowa.
Ten moduł nie wie, że istnieje HTTP, SQL, Grafana ani Home Assistant.

Obiekty domenowe to `@dataclass(slots=True, frozen=True)`, nie modele
pydantic (SPEC.md §5).
"""
