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
