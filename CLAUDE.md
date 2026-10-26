# SmartHeat-Integration — Repo-Kontext

HACS-Integration `smartheat` (Ordner und GitHub-Repo heißen `SmartHeat-Integration`, ältere Dokumente sagen
`SmartHeat-HomeAssistant-Integration`): Einrichtung des Add-ons `heizungsbruecke` über die Einrichtungs-API des
Servers, Status-Entities, Reparatur-Hinweise, Wunschtemperatur und zweiter Wächter (Schritte des Assistenten:
`../docs/module/ha-host.md`, „Integration: Assistent“).

## Struktur und Module

Dateien, Schnittstellen, Daten, Invarianten und Tests der Integration stehen in der Modul-Doku des Dev-Roots, nicht
hier: `../docs/module/ha-host.md` (HA-Oberfläche für die Einrichtung; Discovery und Status-Ereignis als Verträge mit dem
Add-on). Die Einrichtungs-API des Servers beschreibt `../docs/module/einrichtung.md`; Modulkarte und Verträge V1–V5:
`../docs/module/README.md`; Überblick und Datenflüsse: `../docs/architecture.md`. Wer die Integration ändert, zieht
`ha-host.md` im selben Merge nach.

## Prüfen und Branches

```
scripts/check.sh          # lint (mit vulture, Ausnahmen in vulture_ausnahmen.py), test, contract (--only <schritt>)
```
Direkter Aufruf bleibt möglich: `python3.14 -m venv .venv && .venv/bin/pip install -r
requirements_test.txt && .venv/bin/python -m pytest -q` (`pytest-homeassistant-custom-component`
braucht Python >=3.14, daher das explizite `python3.14` beim Venv-Setup; `pytest.ini`:
`asyncio_mode = auto`, `testpaths = tests`). `addon_fakes.py` (Status-Ereignis, Einträge),
`einrichtung_fakes.py` (Fake-Einrichtungs-Server) und `supervisor_fakes.py` (`FakeSupervisor`, Fixture `supervisor`:
Attrappe des `aiohasupervisor`-Clients) sind gemeinsame Test-Doubles. `../tools/contract_check.py` liest aus
diesem Repo `const.py`, `anfrage.py` (Bodies der Einrichtungs-API) und `translations/{de,en}.json` (Verträge V1, V3, V5
und Domäne; Liste: `python3 ../tools/contract_check.py --liste`).

Branches und Releases wie in `../CLAUDE.md` („Arbeitsweise“) und `../docs/ci-cd-runbook.md`. Tag-Muster `vX.Y.Z`
(`-dryrun`-Suffix = Probelauf); ein Push nach `main` ist ein Release, HACS bietet Kunden nur Releases an, nicht den
neuesten `main`-Commit. Echte Tags sind **signiert** (`git tag -s`, SSH-Signatur; Pflicht-Signatur im Tag-Ruleset, zweite
Linie `scripts/ci/tag_pruefen.sh` gegen `.github/allowed_signers` auf `main`) und laufen in der Environment `release`
(Freigabe von Hand); Ablauf und Handgriffe: `../docs/ci-cd-runbook.md` §4 und §9. Die Dev-Werkzeuge installiert die CI
nur aus dem Hash-Lock des Roots (`--require-hashes`, §12).

## Fallen

- Bearer genau als `Authorization: Bearer <token>`; 404/405 der Einrichtungs-API können als HTML kommen (nach
  Statuscode abbilden). Nach angenommenem „entfernen“ sind 401/404 auf der Auftragsroute = „entfernt“.
- Den Gerätezustand gibt `geraet_zustand` im Status-Ereignis, nicht `status` (der Laufzeit-Teil sagt „startet“, solange
  das Add-on nicht konfiguriert ist).
- Add-on-Slugs tragen einen Repo-Hash-Präfix (z. B. `f5f6325b_heizungsbruecke`):
  `supervisor_client.async_resolve_bridge`.
- `manifest.json`-Repo-URL = Name des GitHub-Repos (`SmartHeat-Integration`), nicht der lokale Ordnername.
- Einträge ohne `geraet_id` (VERSION 2) bleiben ohne Migration im Zustand `MIGRATION_ERROR` (seit Stufe 4; kein eigener
  Text mehr); „Neu konfigurieren“ bricht bei ihnen mit `unknown` ab. Die Entity-IDs und `unique_id`s von `client1` (13)
  bleiben (`tests/test_init.py`).
- Ohne Supervisor (`is_hassio`) bricht der Assistent mit `kein_supervisor` ab.
