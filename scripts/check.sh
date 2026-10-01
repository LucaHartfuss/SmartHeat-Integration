#!/usr/bin/env bash
# Lokale und CI-Pruefung (docs/ci-cd-runbook.md). Aufruf: scripts/check.sh [--full] [--only SCHRITT]
# Docker-Schritte: im Add-on laeuft "docker" nur mit --full oder --only docker; in HomeAssistantPapa
# braucht "config" immer Docker. Docker wird direkt genutzt, wenn verfuegbar (CI); in der
# VS-Code-Sandbox nur mit --full ueber flatpak-spawn --host (daher lokal: --only docker --full).
# Ein unbekannter Schrittname bei --only bricht mit Exit 2 ab (gueltige Namen: check_only-Aufruf unten).
set -uo pipefail
cd "$(dirname "$0")/.."
FULL=0
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --full) FULL=1 ;;
    --only) ONLY="${2:?--only braucht einen Schrittnamen}"; shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
  shift
done
FAILED=()
step() {
  local name=$1; shift
  if [ -n "$ONLY" ] && [ "$ONLY" != "$name" ]; then return 0; fi
  echo "=== $name ==="
  if "$@"; then echo "--- $name: OK"; else echo "--- $name: FEHLER"; FAILED+=("$name"); fi
}
finish() {
  if [ ${#FAILED[@]} -eq 0 ]; then echo "=== ALLES GRUEN ==="; exit 0; fi
  echo "=== FEHLER in: ${FAILED[*]} ==="; exit 1
}
# Bricht ab, wenn --only keinen der uebergebenen (gueltigen) Schrittnamen nennt.
check_only() {
  if [ -z "$ONLY" ]; then return 0; fi
  local valid
  for valid in "$@"; do [ "$ONLY" = "$valid" ] && return 0; done
  echo "Unbekannter Schritt: $ONLY (gueltig: $*)" >&2; exit 2
}
# Docker-Befehl: direkt (CI) oder ueber den Host (Sandbox, nur mit --full).
docker_host_run() {
  if command -v docker >/dev/null 2>&1; then "$@"
  elif [ "$FULL" = 1 ] && command -v flatpak-spawn >/dev/null 2>&1; then flatpak-spawn --host "$@"
  else echo "Docker nicht verfuegbar - Schritt nur mit --full (Sandbox) oder in CI"; return 1; fi
}
# Integration braucht Python 3.14 (HA 2026.x). CI setzt PYTHON; lokal laeuft der Host-venv.
if [ -n "${PYTHON:-}" ]; then
  py() { "$PYTHON" "$@"; }
  PYEXE="$("$PYTHON" -c 'import sys; print(sys.executable)')"
elif command -v flatpak-spawn >/dev/null 2>&1; then
  py() { flatpak-spawn --host sh -c 'cd "$1" && shift && exec .venv/bin/python "$@"' _ "$PWD" "$@"; }
  PYEXE="$PWD/.venv/bin/python"
else
  py() { .venv/bin/python "$@"; }
  PYEXE="$PWD/.venv/bin/python"
fi
DEV="${DEV_ROOT:-$(cd .. && pwd)}"
lint() { py -m ruff check . && py -m pyright --pythonpath "$PYEXE" && py scripts/ci/pin_check.py --repo "$PWD"; }
tests() { py -m pytest -q; }
contract() { python3 "$DEV/tools/contract_check.py"; }
check_only lint test contract
step lint lint
step test tests
step contract contract
finish
