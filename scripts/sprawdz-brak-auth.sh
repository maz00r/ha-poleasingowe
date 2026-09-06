#!/usr/bin/env bash
# Blokuje commit, jesli w indeksie jest fixture z sesji zalogowanej.
# SPEC.md §10.2: takie strony zawieraja dane osobowe wlasciciela repo.
set -euo pipefail

znalezione=$(git diff --cached --name-only | grep -- '-auth-' || true)
if [ -n "$znalezione" ]; then
    echo "STOP: proba zacommitowania fixtures z sesji zalogowanej:"
    echo "$znalezione" | sed 's/^/  /'
    echo
    echo "Te pliki zawieraja dane osobowe (SPEC.md §10.2) i sa objete .gitignore."
    echo "Jesli trafily do indeksu, to przez 'git add -f'. Wycofaj je:"
    echo "  git reset HEAD <plik>"
    exit 1
fi
