# Changelog

HACS zeigt den Abschnitt der jeweiligen Version als Release-Notiz. Pro Version ein Abschnitt
`## X.Y.Z`.

## 0.10.0

- Heizgrenze als Stellgröße (TP12h): Die Entity für die Heizgrenze muss eine `number` sein (SmartHeat schreibt
  sie); ein bisher gewählter Sensor fordert „Neu konfigurieren“. Neuer Status-Sensor „Heizgrenze“.
- Voraussetzung: Add-on Heizungsbrücke ab 0.27.0 und SmartHeat-Server mit TP12h (Server vor Add-on vor Integration).

## 0.9.0

- Wizard-Zuordnung sicher (TP12c): Die Heizzone wird nur noch vorgeschlagen, wenn ihr Name den gewählten
  Heizkreis nennt; Schreib-Entities (Heizkurve, Zone, Mindestvorlauf, Heizgrenze, Vorlauf-Soll) müssen zur
  gewählten Heizungs-Integration und Anlage gehören; Abweichungen von der Erkennung und ein
  Aktualisierungsintervall der Heizungs-Integration über 30 Minuten müssen in der Zusammenfassung
  bestätigt werden, die die beschriebenen Entities mit Namen zeigt.
- Pro Home Assistant nur noch ein SmartHeat-Eintrag. Beim Entfernen bleiben Add-ons einer anderen
  SmartHeat-Anlage unberührt; gescheiterte Schritte erscheinen als Benachrichtigung.
- Einrichtung gilt auch bei Datenfehler, Notbetrieb oder inaktivem Abo als abgeschlossen (der
  Abschlusstext nennt den Zustand), ebenso eine Änderung der Optionen. Ein Abbruch nach dem Schreiben
  baut zurück: Ersteinrichtung wie Entfernen, „Neu konfigurieren“ stellt die bisherigen
  Add-on-Einstellungen und das bisherige Profil wieder her. Ein Abbruch vor dem Schreiben nimmt nur die
  Änderungen auf dem Server zurück (neu ausgestellte Zugangsdaten, Profilwechsel), ohne die Add-ons
  anzufassen. Ein erfolgreicher Abschluss entfernt eine Rückbau-Meldung eines früheren Abbruchs. Die
  Optionen stellen bei Ablehnung durch das Add-on den vorigen Stand wieder her.
- Klarere Fehlermeldungen bei Serverproblemen (Zeitlimit 30 s, Ablehnungsgrund des Servers).
- Veraltete Einträge werden allgemein erkannt (Hinweis „Neu konfigurieren“); die alte Entity
  `sensor.smartheat_<anlage>_offset` wird automatisch entfernt.
- Voraussetzung: SmartHeat-Server mit Katalog v2 (vor dem Update der Integration aktualisiert); mit
  einem älteren Server bricht der Assistent mit einem Hinweis ab.
- Neuer abschaltbarer Hinweis „Benachrichtigen, wenn die Therme keine Heizwärme liefert“ (Option
  `notify_hints_off` kennt jetzt `therme`) und das Status-Attribut `waerme_fehlt` am SmartHeat-Status-Sensor
  (Zeitpunkt, seit dem die Therme trotz Wärmeanforderung nichts liefert, sonst leer). Passt zum Add-on 0.26.0.
- Setzt Add-on 0.26.0 voraus (Mindestversion angehoben, weil ältere Add-ons die neue Option `therme` der Hinweis-Auswahl ablehnen).

## 0.8.0

- Regelkern 2.0 (TP11): Die Rolle „Offset“ (Parallelversatz) entfällt zugunsten von zwei eigenen Rollen —
  „Parallelverschiebung“ (`entity_shift_current`, die Zonen-Wunschtemperatur-Entity) und „Mindestvorlauf“
  (`entity_min_flow`); im Schritt „Anlagenwerte“ neu dazu das optionale Vorlauf-Soll
  (`entity_flow_setpoint`). Die Sensoren `sensor.smartheat_<anlage>_parallelverschiebung` und
  `sensor.smartheat_<anlage>_mindestvorlauf` (beide °C) ersetzen `sensor.smartheat_<anlage>_offset`; die
  alte `offset`-Entity wird nicht mehr erzeugt und bleibt als „nicht verfügbar“ in der Entity-Registry
  zurück — kann von Hand gelöscht werden. Voraussetzung: `heizungsbruecke` ≥ 0.24.0.
  **Nach dem Update: Neu konfigurieren ausführen**, sonst geht die Anlage mit „Konfiguration veraltet“
  in den Ruhezustand (kein Schreibzugriff, bis die neuen Anlagenwerte-Felder ausgefüllt sind).
- Wizard und Optionen: Die Heizzone (Parallelverschiebung) kann nicht zugleich die Wunschtemperatur
  liefern — SmartHeat schreibt in die Zone, das ergäbe eine Rückkopplung. Die Zonen-Raumtemperatur
  bleibt als Raumfühler erlaubt.

## 0.7.1

- Wizard und Optionen: Die Entity-Auswahl zeigt nur noch passende Entities — Raumfühler und
  Wunschtemperatur: Thermostate und Sensoren mit der Geräteklasse „Temperatur“; Außentemperatur:
  Temperatursensoren und Wetter; Heizgrenze: `number` und Temperatursensoren; Messwerte unter
  „Erweitert“ nach Geräteklasse (Temperatur, Druck, Energie). Sensoren ohne Geräteklasse erscheinen
  nicht mehr; eine unpassende Entity (z. B. per API) wird mit „Diese Entity passt nicht zu diesem
  Feld“ abgelehnt.

## 0.7.0

- Manifest: `after_dependencies: hassio` (Ladereihenfolge nach dem Supervisor-Modul); Hinweistexte in die Übersetzungskategorie `common` verschoben (keine sichtbare Änderung); Lizenz: MIT.
- Setup-Erkennung liest die Geräteregistry versionsunabhängig (Mapping bis HA 2026.8, iterierbare Sicht ab 2026.9) — keine Deprecation-Warnung mehr ab HA 2026.9.
- HACS: Mindestversion Home Assistant 2026.4.0 (`hacs.json`) — ältere Versionen sind ungetestet.
- Entfernen: Der Server widerruft die Zugangsdaten danach auch dort (`DELETE /tenants/<id>/installation`,
  Best Effort — nicht erreichbar/401/HTTP-Fehler laufen das Entfernen trotzdem zu Ende).
- Entfernen: Scheitert das Zurücksetzen eines laufenden Boosts (Event `abgemeldet` mit `boost` ≠
  `keiner`), bleibt die Heizungsbrücke laufen und wiederholt es selbst (auch nach einem Neustart,
  ab `heizungsbruecke` 0.23.0); nur Cloudflared wird gestoppt.

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
