# SmartHeat-Integration — Repo-Kontext

HACS-Integration `smartheat` (Ordner und GitHub-Repo heißen `SmartHeat-Integration`, ältere Dokumente sagen
`SmartHeat-HomeAssistant-Integration`). Ab 1.0.0 (Spec 5c): Das Add-on `heizungsbruecke` ist ein Gerät des Servers; die
Integration führt den Nutzer über die Einrichtungs-API des Servers (Anmelden → Anlage → Inventur → Zuordnung → Prüfung →
Einrichten), zeigt den Status des Add-ons als Entities, macht seine Hinweise zu Reparatur-Hinweisen, bietet die
Wunschtemperatur an und überwacht das Add-on (zweiter Wächter). Erkennung und Prüfung laufen auf dem Server, die
Konfiguration setzt das Add-on selbst um.

## Struktur und Module

Dateien, Schnittstellen, Daten, Invarianten und Tests der Integration stehen in der Modul-Doku des Dev-Roots, nicht
hier: `../docs/module/ha-host.md` (HA-Oberfläche für die Einrichtung; Discovery und Status-Ereignis als Verträge mit dem
Add-on). Die Einrichtungs-API des Servers beschreibt `../docs/module/einrichtung.md`; Modulkarte und Verträge V1–V5:
`../docs/module/README.md`; Überblick und Datenflüsse: `../docs/architecture.md`. Wer die Integration ändert, zieht
`ha-host.md` im selben Merge nach.

## Prüfen und Branches

```
scripts/check.sh          # lint, test, contract (--only <schritt> für einzelne Schritte)
```
Direkter Aufruf bleibt möglich: `python3.14 -m venv .venv && .venv/bin/pip install -r
requirements_test.txt && .venv/bin/python -m pytest -q` (`pytest-homeassistant-custom-component`
braucht Python >=3.14, daher das explizite `python3.14` beim Venv-Setup; `pytest.ini`:
`asyncio_mode = auto`, `testpaths = tests`). `addon_fakes.py` (Fake-Add-on-Manager, Status-Ereignis, Einträge) und
`einrichtung_fakes.py` (Fake-Einrichtungs-Server) sind gemeinsame Test-Doubles. `../tools/contract_check.py` liest aus
diesem Repo nur `const.py` und `translations/{de,en}.json` (Checks 7, 8, 12, 14, 54, 56, 58–60); der Katalog-Parser und
die Fixture-Kopien bis 0.16 sind mit Plan 5c W Teil B entfallen.

Feature-Branches (`feat/…`/`fix/…`) zweigen von `develop` ab und werden `--no-ff` nach `develop`
gemergt — nie direkt nach `main`. `main` bewegt sich nur per Release-Tag (`vX.Y.Z`,
Fortsetzung der bestehenden Tags `v0.1.0`…`v0.2.2`; `-dryrun`-Suffix = Probelauf) — ein Push nach
`main` ist ein Release, ab dem ersten GitHub-Release bietet HACS Kunden nur noch Releases statt des
neuesten `main`-Commits an. Release-Ablauf, CI-Jobs, Token: `../docs/ci-cd-runbook.md`.

## Regeln und Fallen

- **Status-Ereignis** `smartheat_status`, Schema 3 (Add-on `geraet_status.py`/`smartheat_runtime/status.py` ↔ `const.py`,
  Contract-Check 14): `schema`, Feldliste und erlaubte Werte müssen exakt übereinstimmen. Den Gerätezustand gibt
  `geraet_zustand` (nicht `status`: der Laufzeit-Teil sagt „startet“, solange das Add-on nicht konfiguriert ist).
- **Discovery-Felder** des Add-ons (`DISCOVERY_FIELDS`): `geraet_id`, `zustand` (auch „startet“), `claim_code` (nur
  solange `nicht_uebernommen`). Der Assistent vertraut nur einer Discovery der Heizungsbrücke aus unserem Store-Repository
  (Abgleich über `source`, exakter Slug; `url` und Slug-Endung beweisen nichts) und liest sie vor der Übernahme neu.
- **`EINRICHTUNG_ROUTEN`** (`const.py`) = Routen des Servers (`einrichtung/api.py`, `ROUTEN`). Bearer genau als
  `Authorization: Bearer <token>`; 404/405 können als HTML kommen (nach Statuscode abbilden). Nach angenommenem
  „entfernen“ sind 401/404 auf der Auftragsroute = „entfernt“.
- **Keine Optionen, kein Token im Betrieb:** Die Integration schreibt keine Add-on-Optionen (nur Watchdog/Autostart über
  den Supervisor), hat keinen Optionsdialog und speichert das Einrichtungs-Token nie (nur im Speicher des Flows, nie
  im Log; wird bei jedem Ende beim Server beendet).
- **Wunschtemperatur:** Ereignis `smartheat_wunsch` mit `{"raum_soll": Zahl}`, Grenzen 15–25 °C in 0,5-Schritten.
- **`entry.data`** (`VERSION` 3) = `geraet_id`, `tenant_id`, `profile_id`, `lever_set`, `hebel`, `shift_lever`. Einträge
  aus 0.16 (VERSION 2, ohne `geraet_id`) lehnt das Setup mit Text und Reparatur-Hinweis ab (`alter_eintrag`); den Umzug
  macht Plan 5d. Die Entity-IDs und `unique_id`s von `client1` (13 aus 0.16.1) bleiben (`tests/test_init.py`).
- Add-on-Slugs tragen einen Repo-Hash-Präfix (z. B. `f5f6325b_heizungsbruecke`): `supervisor_client.async_resolve_addons`;
  Mindestversion `MIN_ADDON_VERSIONS` (1.0.0) wird vor dem Warten auf die Discovery geprüft.
- Vom Supervisor nie einen Fehlertext zeigen oder loggen (er kann Optionswerte zitieren), nur die Fehlerart.
- `manifest.json`-Repo-URL = Name des GitHub-Repos (`SmartHeat-Integration`), nicht der lokale Ordnername.
- Ohne Supervisor (`is_hassio`/`SUPERVISOR_TOKEN`) bricht der Assistent früh ab.
