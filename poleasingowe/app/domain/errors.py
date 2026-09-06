"""Wyjątki domenowe (SPEC.md §6.2).

Łapane na granicy use case'u i logowane do `run_log`. Nigdy gołe `except:`.
"""

from __future__ import annotations


class DomainError(Exception):
    """Wspólny przodek — pozwala złapać wyłącznie nasze błędy na granicy."""


class SourceUnavailable(DomainError):
    """Serwis nie odpowiada albo zwrócił odpowiedź, której nie da się użyć."""


class ParseFailed(DomainError):
    """Odpowiedź dotarła, ale nie ma w niej spodziewanej struktury."""


class AuthenticationFailed(DomainError):
    """Logowanie odrzucone. Zwiększa trwały licznik z SPEC.md §10.2."""


class SessionExpired(DomainError):
    """Sesja wygasła — wykryte po treści odpowiedzi, nie po samym kodzie HTTP."""


class DatabaseUnavailable(DomainError):
    """Brak dostępu do bazy. Nigdy nie kończy się crash-loopem (SPEC.md §2)."""
