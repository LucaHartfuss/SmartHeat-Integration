"""Ausnahmeliste fuer vulture: Namen, die vulture fuer tot haelt, die aber von aussen gelesen
oder aufgerufen werden. Je Zeile der Grund. Aufruf: scripts/check.sh --only lint. Vertragskonstanten stehen hier
nicht: ein Treffer dort ist ein Befund (der Contract-Check liest sie, die Integration selbst nicht)."""

# HA-Hooks der Integration (__init__.py): HA ruft sie ueber den Modulnamen auf.
async_setup_entry  # HA-Hook: Integration und jede Plattform (binary_sensor, number, sensor)
async_unload_entry  # HA-Hook: Eintrag entladen
async_remove_entry  # HA-Hook: Eintrag entfernt

# Config-Flow (config_flow.py): HA registriert den Flow ueber domain= und ruft Schritte als async_step_<step_id>.
SmartHeatConfigFlow  # HA-Hook: ConfigFlow-Unterklasse, registriert ueber domain=
VERSION  # HA liest ConfigFlow.VERSION (Migration der Eintraege)
_.async_step_user  # Config-Flow-Schritt: Einstieg ueber die Oberflaeche
_.async_step_hassio  # Config-Flow-Schritt: Supervisor-Discovery des Add-ons
_.async_step_reconfigure  # Config-Flow-Schritt: Neu konfigurieren (HA-Menue)
_.async_step_unterbrechung  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_warten  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_code_abgelaufen  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_uebernehmen  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_nach_discovery  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_inventur_zeit  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_integration  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_fertig  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_zuordnung_aendern  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_geraet_entfernen  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_entfernt  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)
_.async_step_entfernen_laeuft  # Config-Flow-Schritt: HA springt ihn per step_id an (Formular, Menue, Fortschritt)

# Entity-Attribute und -Methoden: HA liest _attr_* ueber die Properties der Basisklassen.
_._attr_unique_id  # HA-Entity-Attribut (Entity.unique_id)
_._attr_translation_key  # HA-Entity-Attribut (Entity.translation_key)
_._attr_device_info  # HA-Entity-Attribut (Entity.device_info)
_._attr_entity_category  # HA-Entity-Attribut (Entity.entity_category)
_._attr_device_class  # HA-Entity-Attribut (SensorEntity.device_class)
_._attr_is_on  # HA-Entity-Attribut (BinarySensorEntity.is_on)
_._attr_native_value  # HA-Entity-Attribut (SensorEntity/NumberEntity.native_value)
_._attr_native_unit_of_measurement  # HA-Entity-Attribut (SensorEntity.native_unit_of_measurement)
_._attr_suggested_display_precision  # HA-Entity-Attribut (SensorEntity.suggested_display_precision)
_._attr_options  # HA-Entity-Attribut (SensorEntity.options, Enum-Sensor)
_._attr_extra_state_attributes  # HA-Entity-Attribut (Entity.extra_state_attributes)
_.async_set_native_value  # HA-Hook: NumberEntity, Dienst number.set_value
