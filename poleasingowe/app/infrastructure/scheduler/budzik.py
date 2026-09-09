"""Budzik pętli dyspozytora, podpinany po jej starcie (SPEC.md §11.1).

Pętla powstaje w zadaniu tła, **już po** zbudowaniu aplikacji — a interfejs
musi mieć czym ją obudzić od pierwszego żądania. Ten sam problem i to samo
rozwiązanie co przy `adaptery_ui` w `main.py`: obiekt tworzymy wcześniej
i uzupełniamy w miejscu, zamiast przebudowywać aplikację po starcie pętli.

Brak podpiętej pętli nie jest awarią. Add-on wstaje też bez niej (§2 pkt 8)
i wtedy prośba o odświeżenie po prostu nic nie robi — dane odświeżą się przy
najbliższym przemiacie.
"""

from __future__ import annotations

from typing import Protocol


class _Budzalny(Protocol):
    def obudz(self) -> None: ...


class BudzikOdroczony:
    """Implementuje port `Budzik`, przekazując prośbę do pętli, gdy ta jest."""

    def __init__(self) -> None:
        self._cel: _Budzalny | None = None

    def podepnij(self, cel: _Budzalny) -> None:
        self._cel = cel

    def obudz(self) -> None:
        if self._cel is not None:
            self._cel.obudz()
