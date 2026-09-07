"""Pomiar własnego procesu (SPEC.md §1.1, §12, §13).

Budżet pamięci z §1.1 ma być **mierzalny, nie deklaratywny** — panel
diagnostyczny i `run_log` czytają RSS stąd.

Bez `psutil`: jedna zależność mniej w obrazie, a wszystko, czego potrzebujemy,
stoi w `/proc/self/status`. Poza Linuksem (czyli na moim Macu przy testach)
zostaje `resource`, którego jednostka różni się między systemami — stąd
jawne przeliczenie zamiast założenia.
"""

from __future__ import annotations

import pathlib
import resource
import sys

STATUS = pathlib.Path("/proc/self/status")


def rss_bajty() -> int | None:
    """Rozmiar rezydentny procesu w bajtach, `None` gdy nie da się zmierzyć."""
    try:
        tresc = STATUS.read_text(encoding="utf-8")
    except OSError:
        return _rss_z_resource()

    for linia in tresc.splitlines():
        if linia.startswith("VmRSS:"):
            czesci = linia.split()
            if len(czesci) >= 2 and czesci[1].isdigit():
                return int(czesci[1]) * 1024  # /proc podaje kibibajty
    return _rss_z_resource()


def _rss_z_resource() -> int | None:
    szczyt = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if szczyt <= 0:
        return None
    # Linux podaje kibibajty, macOS i BSD — bajty. To szczyt, nie stan
    # bieżący, więc na tych systemach liczba jest przybliżeniem.
    return szczyt if sys.platform == "darwin" else szczyt * 1024
