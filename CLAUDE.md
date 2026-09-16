# SmartHeat-HomeAssistant-Integration — Repo-Kontext

HACS-Integration "SmartHeat" (Domain `smartheat`). **Achtung:** das GitHub-Repo heißt `SmartHeat-Integration`, der lokale Ordnername hier weicht bewusst ab — nicht verwechseln, z. B. bei Manifest-URLs. Self-Service-Onboarding-Wizard, konfiguriert automatisch die beiden Add-ons aus `SmartHeat-for-HomeAssistant`. Volle Beschreibung: `../docs/architecture.md`, Abschnitt 5.

## Struktur

- `custom_components/smartheat/config_flow.py` — der Wizard: Login → Tenant → Profil → Entity-Mapping → `provision()` → Add-on-Push (`retry_push` als Fallback-Schritt).
- `api_client.py` — async HTTP-Client gegen `heizungsserver`s Accounts-API (`https://accounts.hartfussha.org`).
- `supervisor_client.py` — schreibt/startet die beiden Add-ons über HA-Core `AddonManager`.
- `const.py` — Rollen-/Label-Mappings (`ROLE_DOMAINS`, `ERZEUGER_TYP_LABELS`, …), Add-on-Repository-URL/Slugs.
- `translations/{de,en}.json`, `strings.json` — vollständige UI-Strings je Wizard-Schritt.

## Tests

```
pip install -r requirements_test.txt
pytest
```
(`pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`.) 29 Tests über 3 Dateien (`test_config_flow.py`, `test_supervisor_client.py`, `test_api_client.py`).

## Besonderheiten

- `const.py`s Rollen-Namen (`ROLE_DOMAINS`) müssen exakt mit `REQUIRED_ROLES`/`ALL_ROLES` in `heizungsserver`/`heizungsbruecke` übereinstimmen (Cross-Repo-Invariante, siehe `../docs/architecture.md` §9).
- Add-on-Slug-Auflösung ist nicht trivial: Supervisor prefixt Custom-Repo-Slugs mit einem Repo-Hash (z. B. `f5f6325b_heizungsbruecke`) — siehe `supervisor_client.py`/`async_resolve_addon_slug()`.
- `manifest.json`-Repo-URL muss zum tatsächlichen Namen des veröffentlichten GitHub-Repos passen (`SmartHeat-Integration`), nicht zum lokalen Ordnernamen dieses Checkouts.
- `async_step_user` bricht früh ab, wenn keine Supervisor-Installation erkannt wird (`is_hassio`/`SUPERVISOR_TOKEN`) — verhindert Provisioning auf Nicht-Supervisor-Installationen.
