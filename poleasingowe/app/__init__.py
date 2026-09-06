"""Add-on Home Assistant zbierający oferty z serwisów aukcji poleasingowych.

Warstwy i kierunek zależności (SPEC.md §6):

    interfaces/       → wywołuje tylko application
    application/      → use case'y, porty (Protocol), Unit of Work
    domain/           → encje, value objects, reguły; ZERO zależności zewnętrznych
    infrastructure/   → adaptery: sources/, persistence/, scheduler/, supervisor/

Granice egzekwuje import-linter (kontrakt w pyproject.toml), a nie dobre chęci.
"""
