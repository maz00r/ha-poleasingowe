"""Porty — kontrakty, które implementuje `infrastructure` (SPEC.md §6.2).

Na tym etapie jest tu wyłącznie `Clock`. Porty źródeł (`AuctionSource`,
`AuthenticatedSource`) i repozytoriów odwołują się do encji domenowych,
których jeszcze nie ma — powstaną razem z nimi w kroku 3 z SPEC.md §14.
Zgodnie z §6.3 nie zakładamy tu interfejsów „na wypadek gdyby kiedyś".
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Zegar jako port (SPEC.md §6.2).

    Żadnego `datetime.now()` poza implementacją tego portu — inaczej
    harmonogram z §11 jest nietestowalny, a `PollingPolicy` przestaje być
    czystą funkcją.

    `now()` zwraca czas **świadomy strefy, w UTC**. Konwersja do strefy
    lokalnej należy wyłącznie do warstwy widoku (§8.2).
    """

    def now(self) -> dt.datetime: ...
