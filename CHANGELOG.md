# Changelog

HACS zeigt den Abschnitt der jeweiligen Version als Release-Notiz. Pro Version ein Abschnitt
`## X.Y.Z`.

## Unveröffentlicht

- Push-Kern: Rote Befunde des Wächters (`addon_gestoppt`, `reagiert_nicht`, `addon_fehlt`) gehen sofort als Push an die
  Handys, die das Status-Ereignis des Add-ons nennt (nur Dienste `notify.mobile_app_*`, höchstens zehn), auch in der
  Ruhezeit; ein fehlgeschlagener Versand wird bis zu 24 h wiederholt, ein offener Befund erinnert alle 24 h. Orangefarbene
  Hinweise gehen einmal hinaus, in der Ruhezeit erst an deren Ende und nur, wenn sie dann noch offen sind; ab dem
  Tageslimit kommt eine Sammelnachricht mit allgemeinem Titel. Gelb wird nie gepusht, `hinweise_aus` schweigt nur
  Nicht-Rot. Ungültige oder fehlende Regeln (Ruhezeit, Tageslimit) fallen auf 22 bis 7 Uhr und 5 zurück. Die Texte
  folgen der Sprache von Home Assistant (de, en); Logs und Pushtexte nennen weder Dienst noch Kreis. Der Zustand liegt im
  Store `smartheat.push` und verschwindet mit dem Eintrag. Versions- und Mehrfach-Befunde lösen keinen Push aus.
- Befunde des zweiten Wächters (Heizungsbrücke gestoppt oder reagiert nicht) erscheinen als Repair unter
  Einstellungen > Reparaturen statt als Benachrichtigung. Das Repair ist ein Fehler, nicht behebbar und bleibt über einen
  Neustart von Home Assistant bestehen; die Integration räumt es beim Start ab, wenn der Wächter dann nichts findet.
  Bei einer Eskalation ändert sich nur sein Text. Das Attribut `grund` des Status-Sensors trägt jetzt den Statuscode
  (`addon_gestoppt`, `reagiert_nicht`) statt des Meldungstextes.
- Zwei neue Wächter-Befunde: `addon_fehlt` (die Heizungsbrücke ist nicht installiert, belegt nur bei genau einem
  Store-Eintrag unseres Repositorys und keinem Add-on mit der Endung `_heizungsbruecke`, erst nach drei Prüfungen in
  Folge; Fehler-Repair, alle Entities außer dem Status sind nicht verfügbar, die Wunschtemperatur lehnt ab) und
  `addon_mehrfach` (mehrfach installiert, sofort, als Warnung; die Entities bleiben verfügbar). Beide lösen keinen
  Neustart des Add-ons aus. Der Befund `addon_fehlt` endet erst, wenn nach seinem Beginn ein Ereignis kam und das Add-on
  nicht mehr als fehlend gilt.
- Die Mindestversion im Repair-Text ist enger gefasst: nur ASCII-Ziffern, Endung nur `-x`, `+x` oder `.devN`. Werte wie
  `1.2.3.com` oder mit Unicode-Ziffern erscheinen nicht mehr im Text; das Repair nimmt dann den Text ohne Version.
- Das Status-Ereignis des Add-ons trägt zwei weitere Anlagenfelder, die die Integration liest: `konfig_version`
  (Version der Konfiguration des Geräts) und `mindest_integration` (kritische Mindestversion der Integration). Ein
  Kreis-Gerät entfällt damit erst, wenn zwei Ereignisse mit derselben Konfigurationsversion den Kreis nicht mehr
  nennen; ohne die Version bleibt es stehen. Das Schema bleibt 4.
- HACS: Mindestversion Home Assistant jetzt 2026.9.4 (`hacs.json`), die Version, gegen die die Tests laufen
  (vorher 2026.4.0, ungeprüft).
- Einrichtungsassistent: Jedes Formularfeld hat jetzt einen Hilfetext (`data_description`, deutsch und englisch).
  `config_flow.py` ist vollständig durch Verhaltenstests abgedeckt (Wartezeiten, Abbrüche, Fehlerrücksprung,
  Entfernen); `scripts/check.sh` bricht unter 100 Prozent für diese Datei ab. Die Regeln `config-flow` und
  `config-flow-test-coverage` der Quality Scale stehen auf erfüllt.
- Qualität: `quality_scale.yaml` weist alle Regeln der Integration Quality Scale mit Stand und Begründung aus (Bronze
  und Silver bis auf die offenen Punkte `brands`, Aktionen und Anleitung);
  das Manifest nennt `integration_type: hub`; jede Plattform setzt `PARALLEL_UPDATES = 0`. Die Testabdeckung wird in
  jedem Testlauf gemessen und bricht unter 95 Prozent ab (heute 98 Prozent).
- Neu: Diagnose-Download für den Eintrag (Einstellungen → Geräte & Dienste → SmartHeat → Diagnose herunterladen). Die
  Datei nennt Versionen (Integration, Add-on, Home Assistant, gelesene Schemata), den Zustand des Wächters, den
  letzten Status mit allen Kreisen und ob der Server verbunden ist. Sie enthält keine Anlagen-ID, keine Namen
  (Kreise heißen „Kreis 1“, „Kreis 2“ …), keine Entity-IDs der Zuordnung, keine Benachrichtigungsdienste, keinen
  Übernahme-Code und keine Texte des Geräts; die Geräte-ID erscheint nur als `shg-` plus sechs Zeichen. Abwesenheit,
  Wochenplan und Leerstand stünden nur als ja/nein darin, nie mit Zeiten.
- Eine bestehende Einrichtung zieht beim ersten Laden auf die neuen Entity-IDs um: die Kreis-Entities
  (z. B. `sensor.smartheat_<name>_niveau`) heißen jetzt `…smartheat_k1_…`, die Entities der Anlage
  (`sensor.smartheat_status` usw.) tragen keinen Anlagennamen mehr. Es bleibt jeweils derselbe Registry-Eintrag:
  Verlauf, vom Kunden vergebene Namen, Bereiche und Sichtbarkeit bleiben erhalten; eine vom Kunden selbst gewählte
  Entity-ID bleibt unverändert. Dashboards und Automationen, die die alten IDs nennen, müssen auf die neuen IDs
  umgestellt werden. Entities ohne Gegenstück in der neuen Version werden entfernt. Vor der ersten Änderung legt die
  Integration ihre eigenen Registry-Einträge und den Konfigurationseintrag in `.storage/smartheat.registry_sicherung`
  ab: der erste Stand vor dem Umzug bleibt dauerhaft, dazu die letzten drei späteren Stände; das Entfernen des Eintrags
  löscht seine Sicherung. Lässt sich die Sicherung nicht schreiben oder lesen, oder scheitert der Umzug, lädt der Eintrag
  nicht (Home Assistant versucht es später erneut) und der Wächter startet erst nach dem Umzug. Die Sicherung ist ein
  Nachweis und eine Hilfe für die Handarbeit, kein Rückweg: vor dem Update eine vollständige Sicherung von Home
  Assistant samt Datenbank anlegen; nur sie bringt die Verlaufsdaten zurück.
- Ein Kreis, den die Anlage nicht mehr meldet, verliert sein Gerät nur, wenn das Ereignis eine Konfigurationsversion
  und mindestens einen Kreis nennt; ein Ereignis ohne Kreise gilt nicht als Beleg für den Wegfall. Die
  Wunschtemperatur eines nicht gemeldeten Kreises lehnt die Entity mit einer eigenen Meldung ab (ruft jemand den Dienst
  auf, überspringt Home Assistant nicht verfügbare Entities stumm).
- Die Mindestversion im Reparatur-Hinweis „Integration veraltet“ erscheint nur in der Form `1.4.0` (mit höchstens einer
  Endung wie `-rc1`); sonst zeigt der Hinweis den Text ohne Version. Reparatur-Hinweise des Geräts mit einem Schlüssel,
  der kein Code ist, entstehen nicht mehr. Der Diagnose-Download nennt vom Datenfehler nur die bekannte Art, zeigt
  in den Reparatur-Hinweisen keine Kennung mit freiem Text, und das Log nennt ein unbekanntes Schema nur als Typ.
- Der Standardserver ist jetzt `https://accounts.setwell.de` (vorher `https://accounts.hartfussha.org`). Die Adresse
  gilt nur für eine neue Einrichtung und nur, wenn die Option `bootstrap_url` des Add-ons Heizungsbrücke nicht gesetzt
  ist; bestehende Einträge sprechen nicht mit dem Server und bleiben unberührt. Das Add-on selbst wechselt beim
  Update mit seinem Standard (siehe sein Changelog).
- Die Einrichtung meldet sich nicht mehr mit E-Mail und Passwort an. Die Integration zeigt einen Anmeldecode und einen
  Link; Sie bestätigen den Code im SmartHeat-Portal, die Einrichtung geht dann von selbst weiter. Dafür fordert die
  Integration vom Add-on einen Besitznachweis des Geräts an (`nachweis <Geräte-ID>`, Ereignis `smartheat_nachweis`):
  erst das Add-on aktualisieren, dann die Integration. Ist die Bestätigung im Portal beim Übernehmen oder Entfernen
  älter als zehn Minuten, beginnt ein neuer Durchlauf mit neuem Code.
- Die Meldung „Add-on Heizungsbrücke läuft nicht“ verspricht keinen Neustart mehr, den der Wächter nach drei Starts in
  einer Stunde verweigert: sie bittet, das Add-on unter Einstellungen → Add-ons zu prüfen, und sagt „wenn möglich,
  startet SmartHeat es neu“ (wie bei „reagiert nicht“).
- Löschen Sie den Eintrag, während der Wächter das Add-on gerade startet, legt die laufende Prüfung die Meldung danach
  nicht mehr neu an und startet das Add-on nicht mehr.
- Die Integration meldet dem Add-on ihre Version mit dem Ereignis `smartheat_integration` (`schema`, `version`,
  `v5_schemas`): beim Laden, mit jeder Wächter-Prüfung (alle 5 Minuten), nach einem Neustart des Add-ons durch den
  Wächter und wenn das Add-on `startet` meldet. Jedes Status-Ereignis des eigenen Geräts zählt jetzt als Lebenszeichen
  des Add-ons, auch wenn die Integration sein Schema nicht kennt: ein neueres Add-on wird nicht mehr nur deshalb neu
  gestartet.
- Sprechen Add-on und Integration kein gemeinsames Schema des Status-Ereignisses, zeigt der Status-Sensor
  `integration_veraltet` (das Add-on ist neuer, die Integration muss aktualisiert werden) oder `addon_veraltet` (das
  Add-on ist älter und muss aktualisiert werden). Dazu erscheint ein Reparatur-Hinweis; bei `integration_veraltet`
  nennt er die Mindestversion der Integration, wenn das Add-on sie mitschickt. Die übrigen Entities sind dann „nicht
  verfügbar“, und die Wunschtemperatur lehnt das Setzen mit einem Text ab, statt zu schreiben. Das nächste lesbare
  Ereignis hebt den Befund auf. Ein unbekannter Status des Add-ons verwirft das Ereignis nicht mehr (der
  Status-Sensor zeigt dann „unbekannt“); die Wunschtemperatur sendet das Schema, das das Add-on zuletzt gemeldet hat.
- Die Anlage ist ein Gerät in Home Assistant (Name der Anlage aus dem Status-Ereignis, sonst „SmartHeat“; Hersteller
  SmartHeat, Modell Home Assistant, Version des Add-ons), und jeder Heizkreis, den die Anlage meldet, bekommt ein
  eigenes Gerät darunter. Das Gerät trägt den Namen des Heizkreis-Abos, sonst „Heizkreis n“. Ein neuer Kreis erscheint
  mit dem nächsten Status-Ereignis; fehlt er in zwei Ereignissen mit derselben Konfigurationsversion, verschwinden sein
  Gerät und dessen Entities. Ein Kreis, den die Anlage nicht mehr meldet, lässt sich auch von Hand löschen. Meldet die
  Anlage mehr als vier Kreise oder eine Kreisnummer über vier, verwirft die Integration das Ereignis (Warnung ohne
  Namen im Log). Das Gerät der Anlage heißt nun nach der Anlage statt „SmartHeat <Anlage>“.
- Die Entities hängen an ihrem Gerät: Status, Datenfehler, letzte Serverantwort, Add-on-Version, Notbetrieb und
  „Server verbunden“ (neu) an der Anlage; Zustand, Kälteschutz, Boost, Abo, Raumtemperatur (neu), Befunde (neu,
  Anzahl) und die Wunschtemperatur an dem Kreis, den sie betreffen. Die Entity-IDs der Kreise lauten
  `<Plattform>.smartheat_k<n>_<Schlüssel>` (zum Beispiel `sensor.smartheat_k1_zustand`), die ihrer Anlage
  `<Plattform>.smartheat_<Schlüssel>`; die `unique_id` trägt Anlage, Kreis und Schlüssel. Hebel und gelernte Werte
  sind Diagnose-Entities und standardmäßig ausgeschaltet. Der Zustand kennt alle Werte des Katalogs, auch die des
  Alltags (`heizt_auf` und weitere). Die Entities eines Kreises entstehen mit dem ersten Status-Ereignis, das den
  Kreis meldet. Bestehende Entities behalten ihre ID, solange der Registry-Eintrag sie trägt; die alten Entities von
  Zustand, Kälteschutz, Boost, Abo, Wunschtemperatur und der Hebel entfernt die Integration beim Laden.
- Meldet der Wächter das Add-on als gestoppt oder nicht reagierend, sind wie beim Versionsbefund alle Entities außer
  dem Status „nicht verfügbar“, die der Anlage und die jedes Heizkreises; die Wunschtemperatur lehnt das Setzen ab.
  Mit der Entwarnung kehren die Werte zurück. Der Übergang steht je einmal im Protokoll (nur der Befund, ohne Namen
  oder Werte).

## 1.1.0

- Das Abmelden beim Löschen des Eintrags (unten) braucht die Heizungsbrücke 1.1.0: erst das Add-on aktualisieren,
  dann die Integration. Mit einem älteren Add-on bleibt es bei der Warnung im Protokoll.
- Das Löschen des Eintrags meldet das Gerät jetzt lokal ab: die Integration schreibt `entfernen <Geräte-ID>` an
  die Heizungsbrücke (Supervisor-stdin), die Anlage geht auf ihre Ursprungswerte zurück und das Portal zeigt
  „Abgemeldet“ (vorher regelte das Gerät still weiter). Kein Server-Aufruf; aus dem Konto entfernt man das Gerät im
  Portal. Ist die Brücke nicht erreichbar oder der Eintrag ohne gültige Geräte-ID, steht eine Warnung im Protokoll
  und der Eintrag ist trotzdem weg. Setzt eine Heizungsbrücke mit stdin-Befehl voraus (Add-on-Änderung desselben
  Pakets). Versehentlich gelöscht: neu einrichten, dann „Neu konfigurieren“ > „Zuordnung ändern“.
- Eine riesige Ganzzahl (etwa 10^400) im Status-Ereignis löst keinen Fehler in der Entity-Aktualisierung mehr aus;
  die Wunschtemperatur und die Hebel- und Lernwert-Sensoren zeigen dann „unbekannt“.
- Einträge aus 0.16 (ohne Geräte-ID) bekommen keinen eigenen Text und keinen Reparatur-Hinweis mehr; Home Assistant
  zeigt den Migrationsfehler. Das Update von 0.16 läuft weiter wie unter 1.0.0 beschrieben (erst den Eintrag löschen).
- Die Anlagenwahl liest das Feld `altpfad` der Einrichtungs-API nicht mehr (der Server sendet es nicht mehr).
- „Neu konfigurieren“ bei einem Eintrag ohne gültige Geräte-ID bricht vor der Anmeldung mit „Unerwarteter Fehler“
  und einer Zeile im Protokoll ab (vorher ein interner Fehler).
- Ein unerwarteter Fehler in einem Warteschritt des Assistenten steht nur noch mit Art und Aufrufstelle im Protokoll,
  nicht mehr mit seinem Text (er konnte Werte des Supervisors oder des Servers enthalten).
- Die Heizungsbrücke wird auch erkannt, wenn das Add-on-Repository mit einem Branch eingetragen ist
  (`https://github.com/LucaHartfuss/SmartHeat-for-HomeAssistant#develop`, etwa im Labor oder als Beta-Tester); als
  Branch zählen nur Buchstaben, Ziffern, `.`, `_`, `-` und `/` (ohne `..` und ohne `/` am Anfang). Ein Fork oder
  eine ähnlich lautende Adresse zählt weiter nicht.
- Eine Add-on-Version, die sich nicht mit der Mindestversion vergleichen lässt, bricht den Assistenten mit „Das
  Add-on Heizungsbrücke ließ sich nicht … prüfen“ ab (vorher ein interner Fehler); die Version steht nicht im
  Protokoll.

## 1.0.0

- **1.0.0** (Spec 5c): Einrichtung über die Supervisor-Discovery des Add-ons und die Einrichtungs-API des Servers;
  Erkennung und Prüfung laufen auf dem Server. Neue Entity Wunschtemperatur (wirkt lokal über das Add-on).
  Reparatur-Hinweise aus dem Gerät. Kein Server-Token im Betrieb, keine Add-on-Optionen. Setzt das Add-on 1.0.0
  voraus. Einträge aus 0.16 (ohne Geräte-ID) werden nicht migriert; das Setup lehnt sie mit Text und
  Reparatur-Hinweis ab: Eintrag löschen (solange 0.16 installiert ist), Add-on 1.0.0 installieren, dann auf 1.0.0
  aktualisieren und neu einrichten.
- **Entfernt:** der Optionsdialog, die Erkennung und Prüfung in der Integration, der Rückbau über die Integration
  und das zweite Add-on `cloudflared_access_mqtt` im Assistenten. „Neu konfigurieren“ bietet stattdessen
  „Zuordnung ändern“ und „Gerät entfernen“.
- Die Entity-IDs von `client1` (13 aus 0.16.1) bleiben, dazu kommt `number.smartheat_<anlage>_wunschtemperatur`.
- Die Wunschtemperatur lehnt eine Eingabe ab, solange das Thermostat ausgeschaltet ist (mit Hinweis).
- Die Server-Adresse aus der Add-on-Option `bootstrap_url` gilt nur noch mit `https` (wie im Add-on).

## 0.16.1

- **Anzeige:** Gelernte Steigung und gelernte Heizgrenze erscheinen mit zwei Nachkommastellen (bisher ungerundet,
  wie vom Server geliefert). Der gespeicherte Wert bleibt unverändert; eine selbst eingestellte Anzeigegenauigkeit
  hat Vorrang.
- Keine Wirkung auf die Regelung, kein „Neu konfigurieren“ nötig.

## 0.16.0

- **Mindestversion des Add-ons:** Die Integration verlangt jetzt das Add-on `heizungsbruecke` ab Version 0.36.0
  (bisher 0.35.0). Zuerst das Add-on aktualisieren, dann die Integration (wie bei 0.15.0).
- **Nachweis der Wärmelieferung:** Im Schritt „Anlagenwerte“ (Abschnitt „Erweitert: Messwerte für Auswertungen“)
  lassen sich optional Betriebsstunden, Starts und Status des Wärmeerzeugers zuordnen. Der SmartHeat-Server erkennt damit, ob die Heizung wirklich Wärme liefert, und lernt nur
  dann. Ohne diese Zuordnung schließt der Server aus der Vorlauftemperatur.
- **Entfernt:** die Meldekategorie „Therme liefert keine Heizwärme“. Den Hinweis gibt jetzt der SmartHeat-Server,
  nicht mehr das Add-on.
- Katalog 4 des Servers (Liefer-Rollen je Hersteller) wird unterstützt.
- Für `client1` ohne Wirkung auf die Regelung; nach dem Update ist kein „Neu konfigurieren“ nötig (`mypyllant` bietet
  keine Betriebsstunden- oder Startzähler).

## 0.15.0

- **Mindestversion des Add-ons:** Die Integration verlangt jetzt das Add-on `heizungsbruecke` ab Version 0.35.0
  (bisher 0.33.0). Der Einrichtungsassistent (auch „Neu konfigurieren“), der Optionsdialog und der Rückbau arbeiten nur
  noch mit diesem oder einem neueren Add-on; bei einem älteren zeigt der Assistent „Das Add-on … ist veraltet“ und
  startet nicht. Die Mindestversion von `cloudflared_access_mqtt` bleibt 1.0.0.
- **Hintergrund:** Ab Add-on 0.34.0 erkennt `heizungsbruecke` bei `weishaupt_modbus` 2.0 die dort geänderte Betriebsart
  „Normal“ und stellt sie richtig um; ältere Add-on-Versionen kennen nur das Schema von 1.x. Ab 0.35.0 endet der
  Comfort-Boost von selbst und der gemerkte Zustand gehört zu Einrichtung und Anlage (Details im Changelog des Add-ons).
- **Reihenfolge bei Bestandsnutzern:** Zuerst das Add-on `heizungsbruecke` auf 0.35.0 aktualisieren, dann die
  Integration. Wer die Integration zuerst aktualisiert, bekommt beim nächsten Öffnen des Assistenten oder des
  Optionsdialogs den Hinweis, dass das Add-on veraltet ist; die Regelung selbst läuft unverändert weiter, weil die
  laufende Integration die Version nur in diesen Dialogen prüft.
- **Einrichtungshinweis im Raum-Schritt** (Assistent und Optionsdialog, Deutsch und Englisch): Die Thermostatköpfe an
  den Heizkörpern des Referenzraums ganz aufdrehen oder mindestens 1 °C über die Wunschtemperatur stellen, sonst
  drosseln sie die Heizung und SmartHeat stellt die Heizkurve zu hoch ein; die Wunschtemperatur sollte kein eigenes
  Zeitprogramm haben, weil häufige Wechsel das Lernen pausieren. Nur Text, keine Änderung an Optionen oder Ablauf.
- Für `client1` ohne Wirkung auf die Regelung; nach dem Update ist kein „Neu konfigurieren“ nötig (Optionsschema und
  Statusereignis unverändert).

## 0.14.0

- **Server-Antwort wird geprüft:** Aus der Antwort des SmartHeat-Servers übernimmt der Assistent nur noch die
  bekannten Profilparameter. Meldet der Server ein anderes Verteilsystem, als Sie gewählt haben, bricht die Einrichtung
  mit einem eigenen Hinweis ab, damit im Add-on keine falschen Sicherheitsgrenzen gelten.
- **Wunschtemperatur:** Die Wunschtemperatur darf nicht von der Heizungsanlage selbst stammen, auch nicht von einer
  anderen Entity desselben Geräts (z. B. dem Sollwert-Sensor der Zone). Ein eigenes Raumthermostat bleibt zulässig.
- **Abbruch von „Neu konfigurieren“:** Hat der abgebrochene Lauf bereits neue Zugangsdaten ausgestellt, bleiben diese
  erhalten; die Add-ons bekommen den gesicherten Stand plus den neuen Zugang, das Profil geht zurück. Bei der
  Ersteinrichtung wird ein neu ausgestellter Zugang jetzt auch dann widerrufen, wenn das Schreiben der Add-on-Optionen
  scheiterte.
- **Profil abgelehnt:** Lehnt der Server einen Profilwechsel ab (Betreiber-Override passt nicht zum neuen Profil), zeigt
  der Assistent den Hinweis „Profil abgelehnt, Support kontaktieren“ statt „später erneut versuchen“.
- **Logs:** Fehler des Supervisors werden nur noch mit ihrem Typnamen geloggt, weil ihr Text Optionswerte zitieren kann.
- Für `client1` ohne Wirkung auf die Regelung; Server und Integration lassen sich in beliebiger Reihenfolge
  aktualisieren (Statusereignis und Optionsschema unverändert). Nach dem Update ist kein „Neu konfigurieren“ nötig.

## 0.13.0

- **Erkennung mit Alternativen je Rolle:** `weishaupt_modbus` 2.0 wird neben 1.x erkannt (Katalog-Feld `alternatives`:
  je Rolle ein primärer Matcher und optionale Alternativen, der erste Treffer gilt; die Heizkreisnummer darf an
  beliebiger Stelle der `unique_id` stehen, in 2.0 als `heating_circuit<n>`). Ältere Integrationen ignorieren das Feld.
  Die Erkennung der Vaillant-Anlagen (`client1`) bleibt unverändert.
- **Herstellerneutrale Texte im Assistenten:** Der Regelhinweis im Schritt „Anlagenwerte“ wird je Hebelsatz angezeigt
  (Vaillant, Weishaupt mit und ohne Heizkennlinie, Viessmann) statt fest auf die Vaillant-Parallelverschiebung
  zugeschnitten. Das Feld heißt „Raum-Soll bzw. Zone“, der Hinweis „manuelle Änderung“ gilt für jeden von SmartHeat
  geregelten Wert, und die Meldung bei nicht bestätigter Abmeldung nennt keine Vaillant-Werte mehr (Deutsch und
  Englisch).
- Für `client1` ohne Wirkung auf die Regelung; Add-on `heizungsbruecke` 0.34.0 und diese Version lassen sich in
  beliebiger Reihenfolge aktualisieren (Statusereignis und Optionsschema unverändert; der Assistent verlangt weiterhin
  mindestens Add-on 0.33.0).

## 0.12.0

- **Hersteller-Abstraktion (Plan 3c):** Katalog v3. Der Wizard erkennt neben myVAILLANT auch Weishaupt (`weishaupt_modbus`)
  und Viessmann (`vicare`); deren Server-Profile sind noch inaktiv, bis die Inventur durch ist (der Wizard bricht dann
  im Systemschritt mit `no_verified_profiles` ab, bevor auf dem Server etwas eingerichtet wird).
- Neuer Schritt „Hebelsatz“ (`lever_set`), nur wenn das Profil mehrere Hebelsätze anbietet (heute Weishaupt:
  `weishaupt_wwp` und der Rückfall `weishaupt_wwp_basis` nur mit dem Raumsoll); vorbelegt ist der Hebelsatz, dessen
  Entities alle erkannt wurden. Die Pflichtfelder im Schritt „Anlagenwerte“ richten sich nach dem Hebelsatz.
- Hersteller-Hinweise im Systemschritt (Text aus dem Katalog), optionales Abfrageintervall (`poll_interval_seconds`) bei
  Herstellern, die es einstellen lassen.
- Status-Entities je Hebel des Hebelsatzes (`heizkurve`, `heizgrenze`, `mindestvorlauf`, `niveau`,
  `parallelverschiebung` bzw. `raum_soll`; die Entity-IDs von `client1` bleiben gleich) und neu `gelernte_steigung`,
  `gelernte_heizgrenze` mit den Lernwerten des Servers. Verwaiste Entities räumt die Integration nur bei vollständigen
  Einträgen auf. Die Hebel-Sensoren heißen jetzt „Heizkurve (Steigung)“ und „Parallelverschiebung“ (vorher
  „Gelernte …“).
- „Neu konfigurieren“ eines unvollständigen Eintrags übernimmt Räume, Raum-Soll, Handys, Batterien und
  abgeschaltete Hinweise aus dem Eintrag; nur die Anlage kommt aus der Erkennung. Ein Eintrag ohne
  Verschiebungshebel (`shift_lever`) gilt als unvollständig.
- Findet der Wizard keinen Heizkreis, nennt der Abbruch den Hersteller-Hinweis (z. B. Weishaupt: Geräte-Postfix leer
  lassen).
- Der Provisioning-Aufruf sendet `client_type` (`ha`) und die Integrationsversion (`client_version`, nur Diagnose).
- Die Hinweis-Schalter kennen `schreibbudget` und `schreibzaehler`; Energiekanal `electrical_total`.
- Voraussetzung: Heizungsbrücke ≥ 0.33.0 (Statusereignis Schema 2) und Server mit Katalog v3 (Server vor Add-on vor
  Integration; Add-on und Integration innerhalb von 15 Minuten nacheinander).
- **Nach dem Update: Neu konfigurieren** (ein Eintrag ohne Hebelsatz gilt als unvollständig, es gibt keinen stillen
  Rückfall).

## 0.11.0

- Provisioning mit Schlüssel/CSR und Transport-Deskriptor (AWS IoT Core vorbereitet): Die Integration erzeugt
  den privaten Schlüssel selbst (er verlässt das Gerät nie), der Server liefert Transport und Zugang, die
  Heizungsbrücke bekommt `transport`, `installation_token` und je nach Art Benutzer/Passwort oder
  Zertifikat/Schlüssel.
- Abmelden und Rückbau mit dem Installations-Token: Das Entfernen widerruft den Zugang mit dem Token aus den
  Add-on-Optionen (Best Effort) und leert alle Zugangs-Optionen der Heizungsbrücke sowie Ziel und
  Service-Token von cloudflared. Der Rückbau von „Neu konfigurieren“ schaltet Watchdog und Boot von
  cloudflared wieder ein, wenn der gesicherte Stand Mosquitto war.
- cloudflared nur beim Mosquitto-Deskriptor: Bei `iot_core` bleibt cloudflared gestoppt, ohne Watchdog und
  ohne Ziel, und der Wächter der Integration ignoriert es.
- Neu konfigurieren provisioniert neu, wenn der Zugang von vor dieser Version stammt oder der Server die
  Transportart gewechselt hat.
- Voraussetzung: Heizungsbrücke ≥ 0.32.0 und SmartHeat-Server mit dem neuen Provisioning (Server vor Add-on
  vor Integration).
- **Jede Installation von vor 0.32.0/0.11.0 (auch `client1`) regelt nach dem Add-on-Update nicht mehr, bis
  „Neu konfigurieren“ ausgeführt wurde** („Konfiguration veraltet“, die Anlage behält ihre letzten Werte).
  Ein neuer Zugang wird nur ausgestellt, wenn der Server schon die Provisioning-Antwort von AWS-1 liefert;
  gegen einen älteren Server nicht „Neu konfigurieren“ ausführen.
- Eigenes Icon (SmartHeat-Flamme) unter `custom_components/smartheat/brand/`; Home Assistant zeigt es
  bei Integrationen und Geräten an (lokale Brand-Bilder, ohne Eintrag im Brands-Repository).

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
