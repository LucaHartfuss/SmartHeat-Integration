# SmartHeat-HomeAssistant-Integration — Repo-Kontext

HACS-Integration "SmartHeat" (Domain `smartheat`). **Achtung:** das GitHub-Repo heißt `SmartHeat-Integration`, der lokale Ordnername hier weicht bewusst ab — nicht verwechseln, z. B. bei Manifest-URLs. Self-Service-Onboarding-Wizard, konfiguriert automatisch die beiden Add-ons aus `SmartHeat-for-HomeAssistant`. Volle Beschreibung: `../docs/architecture.md`, Abschnitt 5.

## Struktur

- `custom_components/smartheat/config_flow.py` — Setup-Wizard 2.0 (Spec `../docs/superpowers/specs/2026-09-25-setup-wizard-2-design.md`): Vorabprüfung der Add-ons (Mindestversion) → Login → Tenant (übersprungen bei genau einem Tenant) → Heizungs-Integration (übersprungen bei genau einer erkannten Integration) → System (Verteilsystem/Erzeugertyp ohne Vorbelegung, außer einem erkannten Erzeugertyp-Vorschlag) → Räume → Anlagenwerte (vorbelegt aus der Erkennung, KPI-Felder in der Sektion „Erweitert“) → Benachrichtigungen (immer gezeigt, auch ohne Handy-App: leeres Formular mit Hinweistext, überwachte Batterien weiterhin gelistet) → Zusammenfassung mit Warnungen → `setup` (Fortschritt: `provision()` erst hier, Add-on-Optionen setzen, beide Add-ons neu starten, Warten auf `sensor.smartheat_<tenant>_status` mit zu diesem Lauf passender `setup_id`) → Eintrag ohne Zugangsdaten, Logout.
- `catalog.py` — parst `GET /catalog` (Integrations-Deskriptoren und verifizierte Profile; eine ungültige Deskriptor-Beschreibung wird verworfen und geloggt, nicht der ganze Katalog).
- `detection.py` — reine Erkennungs-Engine über Registry-/State-Snapshots (Heizkreise, Anlagenrollen, Erzeugertyp-Vorschlag, Wetter-Ersatz, Batterien, Handy-Notify-Services) plus dünner HA-Adapter (`registry_snapshot`, `weather_candidates`, `unit_map`).
- `validation.py` — harte Prüfungen (Existenz, Zahl, °C-Einheit, Plausibilitätsbereiche, Doppelbelegung) und Warnungen für die Zusammenfassung (Quelle veraltet > 6 h, Raumfühler-Abweichung > 3 K).
- `api_client.py` — async HTTP-Client gegen `heizungsserver`s Accounts-API (`https://accounts.hartfussha.org`): Login, Tenants, `GET /catalog`, `provision`, `logout`.
- `supervisor_client.py` — `async_resolve_addons` (löst Slug mit Repo-Hash-Präfix auf, prüft Mindestversion) und `async_get_addon_managers` (ein `AddonManager` je Add-on).
- `const.py` — Rollen (`ROLE_DOMAINS`), Add-on-Optionsnamen, Plausibilitätsbereiche, Mindestversionen, Status-Entity-Konstanten (`status_entity_id()`).
- `translations/{de,en}.json`, `strings.json` — alle Texte inkl. `config.hints` (dynamische Hinweise in der UI-Sprache, gelesen über `translation.async_get_translations`) und `selector` (Beschriftungen von `verteilsystem`/`erzeuger_typ` über `translation_key`, keine `*_LABELS`-Konstanten mehr).

## Tests

```
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest -q
```
(`pytest-homeassistant-custom-component` braucht Python >=3.14, daher das explizite `python3.14` beim Venv-Setup.) (`pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`.) 138 Tests über 7 Dateien (`test_config_flow.py`, `test_supervisor_client.py`, `test_api_client.py`, `test_catalog.py`, `test_detection.py`, `test_validation.py`, `test_const.py`). `tests/fixtures/` enthält Kopien von Server-Daten (`catalog.json`, `client1_mypyllant_registry.json`); Gleichheit mit den echten Server-/Client1-Daten prüft der Contract-Check.

## Besonderheiten

- `const.py`s Rollen-Namen (`ROLE_DOMAINS`) müssen exakt mit `REQUIRED_ROLES`/`ALL_ROLES` in `heizungsserver`/`heizungsbruecke` übereinstimmen (Cross-Repo-Invariante, siehe `../docs/architecture.md` §9); dasselbe gilt für die neuen Add-on-Optionsnamen `room_sensors`/`notify_services`/`battery_entities`/`setup_id` (`OPTION_*`-Konstanten) und für die Status-Entity `sensor.smartheat_<tenant>_status` — Zustände (`startet`/`bereit`/`konfigurationsfehler`) und Attribute (`setup_id`, `grund`) müssen zu `heizungsbruecke/status.py` passen.
- Add-on-Slug-Auflösung ist nicht trivial: Supervisor prefixt Custom-Repo-Slugs mit einem Repo-Hash (z. B. `f5f6325b_heizungsbruecke`) — siehe `supervisor_client.py`/`async_resolve_addons()`.
- `manifest.json`-Repo-URL muss zum tatsächlichen Namen des veröffentlichten GitHub-Repos passen (`SmartHeat-Integration`), nicht zum lokalen Ordnernamen dieses Checkouts.
- `async_step_user` bricht früh ab, wenn keine Supervisor-Installation erkannt wird (`is_hassio`/`SUPERVISOR_TOKEN`) — verhindert Provisioning auf Nicht-Supervisor-Installationen.
- `const.py` (`DEFAULT_HEIZUNGSSERVER_BASE_URL`, `KPI_ENERGY_CHANNELS`, `ROLE_DOMAINS`, Slugs) wird von `python3 ../tools/contract_check.py` gegen Server und Add-on geprüft.
