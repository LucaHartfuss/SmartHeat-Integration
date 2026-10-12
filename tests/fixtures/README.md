# Fixtures

Bleiben bis Plan 5c W Teil B (Contract-Loader): `tools/contract_check.py` im Dev-Root liest diese Dateien noch,
ohne sie bricht der Loader ab (Exit 2). W Teil B B1 stellt die Checks um und entfernt sie zusammen mit
`custom_components/smartheat/catalog.py` und dem Konstanten-Block am Ende von `const.py`.

- `catalog.json` (auch von `tests/test_catalog.py` genutzt)
- `client1_mypyllant_registry.json`
- `addon_schema.json`
- `vicare_registry_from_code.json`, `weishaupt_modbus_registry_from_code.json`,
  `weishaupt_modbus_2_registry_from_code.json`
