"""Zugang der Anlage (Spec AWS-IoT 4.1, 4.2, 5.2): Schluessel und CSR erzeugen, die Provisioning-Antwort
pruefen und auf die Optionen beider Add-ons abbilden. Ohne Home Assistant testbar. Schluessel,
Passwort, Token und Service-Token werden nie geloggt (Regel 6)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .const import (
    CERTIFICATE_CREDENTIAL_OPTIONS,
    CLOUDFLARED_ADDON_SLUG,
    CLOUDFLARED_KEYS,
    CREDENTIAL_FOR_TRANSPORT,
    CREDENTIAL_KEYS,
    DESCRIPTOR_KEYS,
    HEIZUNGSBRUECKE_ADDON_SLUG,
    OPTION_INSTALLATION_TOKEN,
    OPTION_TRANSPORT,
    PASSWORD_CREDENTIAL_OPTIONS,
    PROVISION_RESPONSE_KEYS,
    TRANSPORT_IOT_CORE,
    TRANSPORT_MOSQUITTO,
)

# Bei iot_core: cloudflared ohne Ziel und Token (bleibt installiert und gestoppt, Plan AWS-2 Praez. 1).
CLOUDFLARED_CLEARED_OPTIONS = {"hostname": "", "service_token_id": "", "service_token_secret": ""}


class InvalidProvisioning(ValueError):
    """Die Provisioning-Antwort hat nicht das vereinbarte Format (Text ohne Werte, nur Schluessel)."""


@dataclass(frozen=True)
class Access:
    transport: dict  # Deskriptor fuer die Heizungsbruecke, ohne den cloudflared-Teil
    cloudflared: dict | None = field(repr=False)  # Optionen von cloudflared_access_mqtt, nur bei Mosquitto
    credential: dict = field(repr=False)  # Options-Schluessel -> Wert der Zugangsdaten
    installation_token: str = field(repr=False)
    profile_params: dict | None = None

    @property
    def kind(self) -> str:
        return self.transport["kind"]


def generate_key_and_csr(tenant_id: str) -> tuple[str, str]:
    """EC P-256, CN = tenant_id (Spec 4.1). Der Schluessel verlaesst das Geraet nie (Spec 5.2)."""
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, tenant_id)]))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    return key_pem, csr.public_bytes(serialization.Encoding.PEM).decode()


def _keys(value, expected: frozenset, what: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise InvalidProvisioning(f"{what}: Schluessel {got}, erwartet {sorted(expected)}")
    return value


def _strings(values: dict, what: str) -> dict:
    if not all(isinstance(value, str) and value for value in values.values()):
        raise InvalidProvisioning(f"{what}: leere oder ungueltige Werte")
    return values


def parse_provisioning(body, private_key_pem: str) -> Access:
    body = _keys(body, PROVISION_RESPONSE_KEYS, "Provisioning-Antwort")
    transport = body["transport"]
    kind = transport.get("kind") if isinstance(transport, dict) else None
    if not isinstance(kind, str) or kind not in DESCRIPTOR_KEYS:
        raise InvalidProvisioning(f"Transportart {kind!r} unbekannt")
    transport = _keys(transport, DESCRIPTOR_KEYS[kind], f"Deskriptor {kind}")
    credential_kind = CREDENTIAL_FOR_TRANSPORT[kind]
    credential = _keys(body["credential"], CREDENTIAL_KEYS[credential_kind], "Zugangsdaten")
    if credential["kind"] != credential_kind:
        raise InvalidProvisioning(f"Zugangsdaten {credential['kind']!r} passen nicht zu {kind!r}")
    token = body["installation_token"]
    if not isinstance(token, str) or not token:
        raise InvalidProvisioning("Installations-Token fehlt")
    if not isinstance(body["profile_params"], dict):
        raise InvalidProvisioning("profile_params fehlt")
    if kind == TRANSPORT_MOSQUITTO:
        tunnel = _strings(_keys(transport["cloudflared"], CLOUDFLARED_KEYS, "cloudflared"), "cloudflared")
        bridge_transport = {key: value for key, value in transport.items() if key != "cloudflared"}
        options = _strings({"mqtt_username": credential["username"], "mqtt_password": credential["password"]},
                           "Zugangsdaten")
        cloudflared = {"hostname": tunnel["hostname"], "local_port": transport["port"],
                       "service_token_id": tunnel["service_token_id"],
                       "service_token_secret": tunnel["service_token_secret"]}
    else:
        bridge_transport = dict(transport)
        options = _strings({"tls_certificate": credential["certificate_pem"], "tls_private_key": private_key_pem},
                           "Zugangsdaten")
        cloudflared = None
    return Access(bridge_transport, cloudflared, options, token, body["profile_params"])


def bridge_access_options(access: Access) -> dict:
    """Alle Zugangs-Optionen der Heizungsbruecke; die der anderen Art leer, damit nichts Altes bleibt."""
    options = {key: "" for key in (*PASSWORD_CREDENTIAL_OPTIONS, *CERTIFICATE_CREDENTIAL_OPTIONS)}
    options.update(access.credential)
    options[OPTION_TRANSPORT] = json.dumps(access.transport, sort_keys=True)
    options[OPTION_INSTALLATION_TOKEN] = access.installation_token
    return options


def _bridge_transport(bridge_options: dict) -> dict | None:
    try:
        transport = json.loads(bridge_options.get(OPTION_TRANSPORT) or "")
    except (ValueError, TypeError):
        return None
    kind = transport.get("kind") if isinstance(transport, dict) else None
    if not isinstance(kind, str) or kind not in DESCRIPTOR_KEYS or set(transport) != DESCRIPTOR_KEYS[kind] - {"cloudflared"}:
        return None
    return transport


def transport_kind(bridge_options: dict) -> str | None:
    transport = _bridge_transport(bridge_options)
    return None if transport is None else transport["kind"]


def installation_token(bridge_options: dict) -> str | None:
    token = bridge_options.get(OPTION_INSTALLATION_TOKEN)
    return token if isinstance(token, str) and token else None


def access_from_options(bridge_options: dict, cloudflared_options: dict) -> Access | None:
    """Laufender Zugang aus den Add-on-Optionen (Neu konfigurieren behaelt ihn, Spec TP7 2.3); None, wenn
    er unvollstaendig ist oder von vor AWS-2 stammt -- dann wird neu provisioniert (Review Focus 1)."""
    transport = _bridge_transport(bridge_options)
    token = installation_token(bridge_options)
    if transport is None or token is None:
        return None
    keys = PASSWORD_CREDENTIAL_OPTIONS if transport["kind"] == TRANSPORT_MOSQUITTO else CERTIFICATE_CREDENTIAL_OPTIONS
    credential = {key: bridge_options.get(key) for key in keys}
    if not all(isinstance(value, str) and value for value in credential.values()):
        return None
    cloudflared = None
    if transport["kind"] == TRANSPORT_MOSQUITTO:
        cloudflared = {key: cloudflared_options.get(key)
                       for key in ("hostname", "local_port", "service_token_id", "service_token_secret")}
        if not all(cloudflared.values()):
            return None
    return Access(transport, cloudflared, credential, token)


def watched_addon_slugs(bridge_options: dict) -> frozenset[str]:
    """Add-ons, die laufen muessen: bei iot_core nur die Heizungsbruecke (Plan AWS-2, Praez. 1). Ohne
    lesbaren Transport (Stand vor AWS-2, Ersteinrichtung) wie bisher beide."""
    if transport_kind(bridge_options) == TRANSPORT_IOT_CORE:
        return frozenset({HEIZUNGSBRUECKE_ADDON_SLUG})
    return frozenset({HEIZUNGSBRUECKE_ADDON_SLUG, CLOUDFLARED_ADDON_SLUG})
