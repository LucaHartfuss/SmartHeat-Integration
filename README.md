# SmartHeat fuer Home Assistant

Native Config-Flow-Integration fuer die SmartHeat-Add-ons (`heizungsbruecke`,
`cloudflared_access_mqtt` aus dem Repo
[SmartHeat-for-HomeAssistant](https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant)).

## Voraussetzungen

- Home Assistant mit Supervisor (Hass.io/HAOS).
- Beide Add-ons aus dem SmartHeat-Add-on-Repository installiert: `heizungsbruecke` ≥ 0.24.0
  und `cloudflared_access_mqtt` ≥ 1.0.0 (nicht manuell konfigurieren — der Wizard prueft die
  Mindestversion vor jedem Setup und bricht sonst mit einer Fehlermeldung ab).
- Eine unterstuetzte Heizungs-Integration in Home Assistant eingerichtet (z. B. myVAILLANT).
- **Aktualisierungsintervall der Heizungs-Integration:** hoechstens 30 Minuten (myVAILLANT: Standard 30 Minuten). Bei einem laengeren Intervall erkennt SmartHeat eigene Aenderungen womoeglich zu spaet; der Wizard weist darauf hin.

## Installation

1. Add-ons und Heizungs-Integration wie oben installieren.
2. Dieses Repository als HACS-Custom-Repository hinzufuegen, "SmartHeat" installieren.
3. Einstellungen → Geraete & Dienste → Integration hinzufuegen → "SmartHeat" → Setup-Wizard
   ausfuellen (Login → Tenant → Heizungs-Integration → System → Raeume → Anlagenwerte →
   Benachrichtigungen → Zusammenfassung).

Die Integration erkennt Heizkreis, Anlagenwerte und Raumfuehler weitgehend automatisch, schreibt
die Konfiguration nach der Zusammenfassung in beide Add-ons und startet sie neu.

## Nach der Einrichtung

- **Gerät „SmartHeat <Anlage>“:** Status (regelt, Notbetrieb, Datenfehler, Abo, Add-on gestoppt …), Notbetrieb,
  Datenfehler mit Quelle, Boost, letzte Serverantwort, gelernte Heizkurve, gelernte Parallelverschiebung,
  Mindestvorlauftemperatur, Abo, Add-on-Version. Ausgefallene Raumfühler, schwache Batterien und manuelle
  Eingriffe stehen als Attribute am Status.
- **Optionen** (ohne Anmeldung): Raumfühler, Wunschtemperatur, Handys, Batterien, Hinweise einzeln abschalten.
- **Neu konfigurieren** (mit Anmeldung): Heizkreis, Verteilsystem, Anlagenwerte (u. a. die Zone für die
  Parallelverschiebung, Mindestvorlauf-Entity, optional das Vorlauf-Soll). Die Zugangsdaten bleiben
  (fehlen sie in den Add-ons, werden neue ausgestellt); Batterieauswahl und Handys aus den Optionen bleiben.
- **Überwachung:** Die Integration schaltet Watchdog und „Start beim Booten“ für beide Add-ons ein, prüft alle
  5 Minuten, ob sie laufen und sich melden, meldet Ausfälle und startet sie neu (höchstens dreimal pro Stunde).
- **Entfernen:** Das Add-on wird abgemeldet (ein laufender Boost wird zurückgesetzt), beide Add-ons werden
  gestoppt und ihre Zugangsdaten geleert. Scheitert das Zurücksetzen, läuft die Heizungsbrücke weiter, bis
  es gelingt, und meldet die Werte, die sonst von Hand einzustellen sind. Ist der Server erreichbar, widerruft er die Zugangsdaten dort
  ebenfalls (Best Effort — scheitert das, bleiben sie bis zur nächsten Einrichtung gültig).

## Update auf 0.9.0

Wizard-Zuordnung sicher (TP12c): Die Heizzone wird jetzt genauer erkannt (Name muss den Heizkreis nennen)
und die Heizwerte müssen zur gewählten Integration passen; außerdem können entstandene Konfigurationsfehler
erkannt und automatisch bereinigt werden. **Pro Home Assistant nur noch ein SmartHeat-Eintrag** — bei
mehreren älteren Einträgen darauf achten, welche Add-ons zu welcher Anlage gehören (Entfernen behält Add-ons
anderer Einträge). Eine als „Neu konfigurieren” gekennzeichnete Konfiguration wird dabei ohne Anmeldung
wiederhergestellt.

### Updates von 0.7.x

Ab 0.8.0/`heizungsbruecke` 0.24.0 ersetzt die Rolle „Parallelverschiebung” (Zonen-Wunschtemperatur) die
bisherige Rolle „Offset”; der Sensor `sensor.smartheat_<anlage>_offset` entfällt zugunsten von
`sensor.smartheat_<anlage>_parallelverschiebung` und `sensor.smartheat_<anlage>_mindestvorlauf`. Eine
Konfiguration aus einer älteren Version startet zwar noch, geht aber ohne Schreibzugriff auf die Anlage in
den Ruhezustand „Konfiguration veraltet” über. **Nach dem Update: Neu konfigurieren** ausführen und dabei
die neuen Anlagenwerte-Felder ausfüllen.

## Änderungen

Alle Versionen stehen in [CHANGELOG.md](CHANGELOG.md) (HACS zeigt den jeweiligen Abschnitt als
Release-Notiz).
