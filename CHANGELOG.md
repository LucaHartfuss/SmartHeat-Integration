# Changelog

HACS zeigt den Abschnitt der jeweiligen Version als Release-Notiz. Pro Version ein Abschnitt
`## X.Y.Z`.

## Unveröffentlicht

- Manifest: `after_dependencies: hassio` (Ladereihenfolge nach dem Supervisor-Modul); Hinweistexte in die Übersetzungskategorie `common` verschoben (keine sichtbare Änderung); Lizenz: MIT.

## 0.6.0

Status-Entities statt der Status-Entity des Add-ons (gespeist über das Event `smartheat_status`), zweiter
Wächter für beide Add-ons, Watchdog/Boot automatisch, Optionen ohne Login, „Neu konfigurieren“ ohne neue
Zugangsdaten (Server-Endpunkt `POST /tenants/<id>/profile`), Reauth bei abgelehnten Zugangsdaten, Abmelden
beim Entfernen. Einträge aus 0.4.x/0.5.x werden als unvollständig übernommen; ein Reparaturhinweis führt zu
„Neu konfigurieren“. Voraussetzung: `heizungsbruecke` ≥ 0.20.0.

## 0.5.0

Setup-Wizard 2.0: automatische Erkennung von Heizkreis, Anlagenrollen, Erzeugertyp-Vorschlag,
Raumfuehler-Batterien und Handy-Benachrichtigungen aus Registry/State statt manueller
Entity-Zuordnung; Vorabpruefung der Add-on-Mindestversionen vor dem Login; `provision()` erst im
letzten Schritt, nach allen Pruefungen und einer bestaetigten Zusammenfassung mit Warnungen
(veraltete Quellen, abweichende Raumfuehler); Warten auf die Add-on-Status-Entity mit einer pro
Lauf frischen `setup_id`, damit kein veralteter Status eines vorigen Versuchs gelesen wird;
Logout nach erfolgreichem Setup. Die Schritte `profile`/`entities`/`kpi_metrics`/`retry_push` aus
0.3.0/0.4.0 entfallen ersatzlos.

Voraussetzung: Add-on `heizungsbruecke` ≥ 0.19.0 und `cloudflared_access_mqtt` ≥ 1.0.0 — eine
aeltere Add-on-Konfiguration (z. B. noch mit `entity_room_actual`) wird vom Add-on selbst als
veraltet abgelehnt.

## 0.3.0

Optionaler letzter Onboarding-Schritt für KPI-Entwicklungs-Metriken (Vorlauf/Rücklauf/Betriebsmodus/Wasserdruck/Effizienz/Energie pro Kanal), profilabhängig, komplett übersprungen wenn das Server-Profil keine telemetry_capabilities ausweist.

Voraussetzung: Diese Integrationsversion darf nur zusammen mit bzw. nach dem Add-on `heizungsbruecke` ab Version 0.13.0 eingesetzt werden — ältere Add-on-Versionen lehnen die neuen KPI-Optionen ab. Das Onboarding schlägt beim Schreiben der Add-on-Konfiguration nur dann fehl, wenn im KPI-Schritt tatsächlich Felder zugeordnet wurden; ein leerer KPI-Schritt funktioniert auch mit älteren Add-ons.
