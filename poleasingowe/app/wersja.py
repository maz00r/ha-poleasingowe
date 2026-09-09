"""Wersja dodatku — jedna liczba dla całego projektu.

Stoi tu, a nie w którejś z warstw, bo nie należy do żadnej: to metadana
opakowania. Ta sama liczba musi być w `config.yaml` (widzi ją Supervisor)
i w etykiecie `io.hass.version` Dockerfile'a; pilnuje tego
`test_pakowanie_addonu.py`.

Interfejs dokleja ją do adresów arkusza stylów i HTML-a jako znacznik
cache'u. Bez tego przeglądarka po aktualizacji dodatku pokazuje **stary**
arkusz do nowego HTML-a — a to wygląda dokładnie jak zepsuty panel.
"""

from __future__ import annotations

WERSJA = "0.18.0"
