# SmartHeat fuer Home Assistant

Native Config-Flow-Integration fuer die SmartHeat-Add-ons (`heizungsbruecke`,
`cloudflared_access_mqtt` aus dem Repo
[SmartHeat-for-HomeAssistant](https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant)).

## Voraussetzungen

- Home Assistant mit Supervisor (Hass.io/HAOS).
- Beide Add-ons aus dem SmartHeat-Add-on-Repository installiert: `heizungsbruecke` ≥ 0.19.0
  und `cloudflared_access_mqtt` ≥ 1.0.0 (nicht manuell konfigurieren — der Wizard prueft die
  Mindestversion vor jedem Setup und bricht sonst mit einer Fehlermeldung ab).
- Eine unterstuetzte Heizungs-Integration in Home Assistant eingerichtet (z. B. myVAILLANT).

## Installation

1. Add-ons und Heizungs-Integration wie oben installieren.
2. Dieses Repository als HACS-Custom-Repository hinzufuegen, "SmartHeat" installieren.
3. Einstellungen → Geraete & Dienste → Integration hinzufuegen → "SmartHeat" → Setup-Wizard
   ausfuellen (Login → Tenant → Heizungs-Integration → System → Raeume → Anlagenwerte →
   Benachrichtigungen → Zusammenfassung).

Die Integration erkennt Heizkreis, Anlagenwerte und Raumfuehler weitgehend automatisch, schreibt
die Konfiguration nach der Zusammenfassung in beide Add-ons und startet sie neu.

## Änderungen

### 0.5.0

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

### 0.3.0

Optionaler letzter Onboarding-Schritt für KPI-Entwicklungs-Metriken (Vorlauf/Rücklauf/Betriebsmodus/Wasserdruck/Effizienz/Energie pro Kanal), profilabhängig, komplett übersprungen wenn das Server-Profil keine telemetry_capabilities ausweist.

Voraussetzung: Diese Integrationsversion darf nur zusammen mit bzw. nach dem Add-on `heizungsbruecke` ab Version 0.13.0 eingesetzt werden — ältere Add-on-Versionen lehnen die neuen KPI-Optionen ab. Das Onboarding schlägt beim Schreiben der Add-on-Konfiguration nur dann fehl, wenn im KPI-Schritt tatsächlich Felder zugeordnet wurden; ein leerer KPI-Schritt funktioniert auch mit älteren Add-ons.
