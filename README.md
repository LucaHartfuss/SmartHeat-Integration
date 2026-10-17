# SmartHeat fuer Home Assistant

HACS-Integration fuer das SmartHeat-Add-on `heizungsbruecke` aus dem Repo
[SmartHeat-for-HomeAssistant](https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant). Das Add-on ist ein
Geraet des SmartHeat-Servers; die Integration richtet es ueber die Einrichtungs-API des Servers ein und zeigt seinen
Status in Home Assistant. Die Regelung selbst laeuft auf dem Server und im Add-on, nicht in der Integration.

## Voraussetzungen

- Home Assistant mit Supervisor (Hass.io/HAOS).
- Eine unterstuetzte Heizungs-Integration in Home Assistant eingerichtet (z. B. myVAILLANT).
- Ein SmartHeat-Konto.
- Das Add-on `heizungsbruecke` ab Version 1.0.0. Eine aeltere Version lehnt der Assistent mit einem Hinweis ab.

## Installation

1. Dieses Repository als HACS-Custom-Repository hinzufuegen und "SmartHeat" installieren, Home Assistant neu starten.
2. Das Add-on-Repository `https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant` im Add-on-Store eintragen und
   `heizungsbruecke` installieren und starten. (Fehlt das Add-on, installiert und startet es der Assistent selbst.)
3. Einstellungen → Geraete & Dienste: "SmartHeat gefunden" bestaetigen (Supervisor-Discovery des Add-ons) und dem
   Assistenten folgen: Anmelden → Anlage → (Inventur laeuft) → Heizungs-Integration → System → ggf. Hebelsatz →
   Raeume → Anlagenwerte → Benachrichtigungen → Zusammenfassung → Einrichten. Alternativ "Integration hinzufuegen" →
   "SmartHeat".

Inventur, Vorschlag und Pruefung laufen auf dem Server; der Assistent zeigt die Ergebnisse an und nimmt Korrekturen
entgegen. Die Konfiguration uebernimmt das Add-on selbst, die Integration schreibt keine Add-on-Optionen und haelt
keinen Server-Token im Betrieb (das Einrichtungs-Token lebt nur im Speicher des Assistenten).

## Entities

Geraet "SmartHeat <Anlage>":

- Status (regelt, Notbetrieb, Datenfehler, Abo, Add-on gestoppt …), Datenfehler mit Quelle, Boost, letzte
  Serverantwort, Abo, Add-on-Version.
- Aktuelle Hebel: Heizkurve, Parallelverschiebung (bzw. Niveau oder Raum-Soll), Heizgrenze, Mindestvorlauf (je nach
  Hebelsatz); dazu gelernte Steigung und gelernte Heizgrenze.
- Notbetrieb (Binary-Sensor).
- **Wunschtemperatur** (`number`): wirkt lokal ueber das Add-on, auch ohne Server.

Hinweise des Geraets (z. B. Update noetig, Geraet gesperrt, Einstellung abgelehnt) erscheinen als Reparatur-Hinweise.

## Neu konfigurieren und Entfernen

- **Neu konfigurieren** (mit Anmeldung): "Zuordnung aendern" fuehrt durch die Zuordnung der Anlagenwerte erneut,
  "Geraet entfernen" loest das Geraet beim Server.
- **Eintrag loeschen** loest nichts beim Server und stoppt das Add-on nicht; das Geraet regelt weiter. Das Geraet
  entfernen geht ueber "Neu konfigurieren" oder das Portal.
- Es gibt keinen Optionsdialog.

## Ueberwachung

Die Integration schaltet Watchdog und "Start beim Booten" fuer die Heizungsbruecke ein, prueft alle 5 Minuten, ob sie
laeuft und sich meldet, meldet Ausfaelle und startet sie neu (hoechstens dreimal pro Stunde).

## Aenderungen

Alle Versionen stehen in [CHANGELOG.md](CHANGELOG.md) (HACS zeigt den jeweiligen Abschnitt als
Release-Notiz). Update von 0.16: Abschnitt 1.0.0 dort.
