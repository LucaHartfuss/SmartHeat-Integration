# SmartHeat fuer Home Assistant

Native Config-Flow-Integration fuer die SmartHeat-Add-ons (`heizungsbruecke`,
`cloudflared_access_mqtt` aus dem Repo
[SmartHeat-for-HomeAssistant](https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant)).

## Installation

1. Beide Add-ons aus dem SmartHeat-Add-on-Repository installieren (nicht manuell konfigurieren).
2. Dieses Repository als HACS-Custom-Repository hinzufuegen, "SmartHeat" installieren.
3. Einstellungen → Geraete & Dienste → Integration hinzufuegen → "SmartHeat" → Formular
   ausfuellen.

Die Integration schreibt die Konfiguration in beide Add-ons und startet sie automatisch.

## Änderungen

### 0.3.0

Optionaler letzter Onboarding-Schritt für KPI-Entwicklungs-Metriken (Vorlauf/Rücklauf/Betriebsmodus/Wasserdruck/Effizienz/Energie pro Kanal), profilabhängig, komplett übersprungen wenn das Server-Profil keine telemetry_capabilities ausweist.

Voraussetzung: Diese Integrationsversion darf nur zusammen mit bzw. nach dem Add-on `heizungsbruecke` ab Version 0.13.0 eingesetzt werden — ältere Add-on-Versionen lehnen die neuen KPI-Optionen ab. Das Onboarding schlägt beim Schreiben der Add-on-Konfiguration nur dann fehl, wenn im KPI-Schritt tatsächlich Felder zugeordnet wurden; ein leerer KPI-Schritt funktioniert auch mit älteren Add-ons.
