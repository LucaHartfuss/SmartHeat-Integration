# SmartHeat-Integration — Repo-Kontext

HACS-Integration `smartheat` (Ordner und GitHub-Repo heißen `SmartHeat-Integration`, ältere Dokumente sagen
`SmartHeat-HomeAssistant-Integration`): Einrichtungsassistent (Login → Tenant → Heizungs-Integration → System →
Hebelsatz → Räume → Anlagenwerte → Setup), richtet die Add-ons aus `SmartHeat-for-HomeAssistant` ein, zeigt den Status
als Entities und überwacht die Add-ons (zweiter Wächter). Assistent, „Neu konfigurieren“, Reauth, Rückbau und Module:
`../docs/architecture.md` Abschnitt 6.

## Prüfen und Branches

```
scripts/check.sh          # lint, test, contract (--only <schritt> für einzelne Schritte)
```
Direkter Aufruf bleibt möglich: `python3.14 -m venv .venv && .venv/bin/pip install -r
requirements_test.txt && .venv/bin/python -m pytest -q` (`pytest-homeassistant-custom-component`
braucht Python >=3.14, daher das explizite `python3.14` beim Venv-Setup; `pytest.ini`:
`asyncio_mode = auto`, `testpaths = tests`). `addon_fakes.py`/`flow_helpers.py` sind gemeinsame
Test-Doubles (Fake-Supervisor/Add-on-Manager, Fake-HA-Bus). `tests/fixtures/` enthält Kopien von Server-Daten
(`catalog.json`, `client1_mypyllant_registry.json`); Gleichheit mit den echten
Server-/Client1-Daten prüft der Contract-Check.

Feature-Branches (`feat/…`/`fix/…`) zweigen von `develop` ab und werden `--no-ff` nach `develop`
gemergt — nie direkt nach `main`. `main` bewegt sich nur per Release-Tag (`vX.Y.Z`,
Fortsetzung der bestehenden Tags `v0.1.0`…`v0.2.2`; `-dryrun`-Suffix = Probelauf) — ein Push nach
`main` ist ein Release, ab dem ersten GitHub-Release bietet HACS Kunden nur noch Releases statt des
neuesten `main`-Commits an. Release-Ablauf, CI-Jobs, Token: `../docs/ci-cd-runbook.md`.

## Regeln und Fallen

- **Status-Event-Vertrag** `smartheat_status` (Add-on `smartheat_runtime/status.py` und `heizungsbruecke/ha_sinks.py` ↔
  `const.py`, Contract-Check 14): `schema`, Feldliste und erlaubte Werte müssen exakt übereinstimmen.
- **Hebel-, Rollen- und Optionsnamen** in `const.py` (`LEVER_OPTIONS`, `LEVER_SETS`, `ROLE_DOMAINS`, `OPTION_*`) müssen zu
  Server und Add-on passen (Checks 4, 25, 26, 42; `architecture.md` §9). `const.py` prüft `python3 ../tools/contract_check.py`.
- **`entry.data`** = Anlagenwerte, Tenant, Profil-ID (ändern nur Assistent und „Neu konfigurieren“); **`entry.options`** =
  veränderliche Einstellungen (Optionen-Flow ohne Login). Der Eintrag speichert keine Zugangsdaten; nie Geheimnisse loggen.
- **Unvollständige Einträge** (`const.entry_incomplete`) bekommen einen nicht-fixbaren Repair-Issue und behalten ihre alten
  Entities bis „Neu konfigurieren“.
- Add-on-Slugs tragen einen Repo-Hash-Präfix (z. B. `f5f6325b_heizungsbruecke`): `supervisor_client.async_resolve_addons`.
- `manifest.json`-Repo-URL = Name des GitHub-Repos (`SmartHeat-Integration`), nicht der lokale Ordnername.
- Ohne Supervisor (`is_hassio`/`SUPERVISOR_TOKEN`) bricht der Assistent früh ab.
