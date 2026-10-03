import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.smartheat import provisioning
from custom_components.smartheat.const import CLOUDFLARED_ADDON_SLUG, HEIZUNGSBRUECKE_ADDON_SLUG

CA = "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n"
CERT = "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----\n"
MOSQUITTO_BODY = {
    "transport": {"kind": "mosquitto_cloudflared", "host": "127.0.0.1", "port": 18830,
                  "cloudflared": {"hostname": "mqtt.example.org", "service_token_id": "cf-id", "service_token_secret": "cf-secret"}},
    "credential": {"kind": "password", "username": "wohnung1_a1", "password": "mqtt-geheim"},
    "installation_token": "tok-1",
    "profile_params": {"verteilsystem": "Heizkoerper", "daily_trigger_time": "12:10"},
}
IOT_BODY = {
    "transport": {"kind": "iot_core", "host": "abc-ats.iot.eu-central-1.amazonaws.com", "port": 8883, "alpn": None,
                  "ca_pem": CA, "client_id": "wohnung1"},
    "credential": {"kind": "certificate", "certificate_pem": CERT},
    "installation_token": "tok-2",
    "profile_params": {"verteilsystem": "Heizkoerper", "daily_trigger_time": "12:10"},
}


def test_key_and_csr_are_ec_p256_with_the_tenant_as_cn():
    key_pem, csr_pem = provisioning.generate_key_and_csr("wohnung1")
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    assert csr.is_signature_valid and isinstance(csr.public_key().curve, ec.SECP256R1)
    assert csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "wohnung1"
    assert key_pem.startswith("-----BEGIN PRIVATE KEY-----")


def test_mosquitto_answer_keeps_the_service_token_out_of_the_bridge_and_drops_the_key():
    access = provisioning.parse_provisioning(MOSQUITTO_BODY, "KEY")
    options = provisioning.bridge_access_options(access)
    assert json.loads(options["transport"]) == {"kind": "mosquitto_cloudflared", "host": "127.0.0.1", "port": 18830}
    assert options["mqtt_username"] == "wohnung1_a1" and options["mqtt_password"] == "mqtt-geheim"
    assert options["tls_certificate"] == "" and options["tls_private_key"] == ""
    assert options["installation_token"] == "tok-1"
    assert "cf-secret" not in json.dumps(options)
    assert access.cloudflared == {"hostname": "mqtt.example.org", "local_port": 18830,
                                  "service_token_id": "cf-id", "service_token_secret": "cf-secret"}


def test_iot_answer_puts_certificate_and_local_key_into_the_bridge():
    access = provisioning.parse_provisioning(IOT_BODY, "KEY-PEM")
    options = provisioning.bridge_access_options(access)
    assert options["tls_certificate"] == CERT and options["tls_private_key"] == "KEY-PEM"
    assert options["mqtt_username"] == "" and options["mqtt_password"] == ""
    assert access.cloudflared is None and access.kind == "iot_core"


@pytest.mark.parametrize("mutate", [
    lambda b: b.pop("installation_token"),
    lambda b: b.update(username="alt"),
    lambda b: b["transport"].update(kind="rabbitmq"),
    lambda b: b["transport"].update(kind=[]),
    lambda b: b["transport"].update(kind={"a": 1}),
    lambda b: b["transport"].pop("host"),
    lambda b: b["credential"].update(kind="certificate"),
    lambda b: b["credential"].pop("password"),
    lambda b: b.update(installation_token=""),
    lambda b: b.update(profile_params=None),
    lambda b: b["transport"]["cloudflared"].pop("service_token_secret"),
])
def test_unusable_answers_are_rejected(mutate):
    body = json.loads(json.dumps(MOSQUITTO_BODY))
    mutate(body)
    with pytest.raises(provisioning.InvalidProvisioning):
        provisioning.parse_provisioning(body, "KEY")


def test_access_round_trips_through_the_add_on_options():
    access = provisioning.parse_provisioning(MOSQUITTO_BODY, "KEY")
    kept = provisioning.access_from_options(provisioning.bridge_access_options(access), access.cloudflared)
    assert kept is not None and kept.kind == access.kind and kept.installation_token == "tok-1"
    assert provisioning.bridge_access_options(kept) == provisioning.bridge_access_options(access)


@pytest.mark.parametrize("bridge_options", [
    {"mqtt_username": "client1_alt", "mqtt_password": "alt"},  # Stand vor AWS-2
    {"transport": "kein json", "installation_token": "t", "mqtt_username": "u", "mqtt_password": "p"},
    {"transport": json.dumps({"kind": "mosquitto_cloudflared", "host": "h", "port": 1}), "mqtt_username": "u", "mqtt_password": "p"},
])
def test_incomplete_access_means_provision_again(bridge_options):
    cloudflared = {"hostname": "h", "local_port": 18830, "service_token_id": "i", "service_token_secret": "s"}
    assert provisioning.access_from_options(bridge_options, cloudflared) is None


def test_mosquitto_access_without_service_token_is_incomplete():
    access = provisioning.parse_provisioning(MOSQUITTO_BODY, "KEY")
    assert provisioning.access_from_options(
        provisioning.bridge_access_options(access), {**access.cloudflared, "service_token_secret": ""}) is None


def test_watched_add_ons_follow_the_transport():
    mosquitto = provisioning.bridge_access_options(provisioning.parse_provisioning(MOSQUITTO_BODY, "K"))
    iot = provisioning.bridge_access_options(provisioning.parse_provisioning(IOT_BODY, "K"))
    assert provisioning.watched_addon_slugs(mosquitto) == {HEIZUNGSBRUECKE_ADDON_SLUG, CLOUDFLARED_ADDON_SLUG}
    assert provisioning.watched_addon_slugs(iot) == {HEIZUNGSBRUECKE_ADDON_SLUG}
    assert provisioning.watched_addon_slugs({}) == {HEIZUNGSBRUECKE_ADDON_SLUG, CLOUDFLARED_ADDON_SLUG}


def test_secrets_are_not_in_the_repr():
    access = provisioning.parse_provisioning(MOSQUITTO_BODY, "KEY")
    text = repr(access)
    assert "mqtt-geheim" not in text and "tok-1" not in text and "cf-secret" not in text


@pytest.mark.parametrize("kind", [[], {"a": 1}, 7, None])
def test_an_unhashable_or_non_string_transport_kind_is_not_a_crash(kind):
    options = {"transport": json.dumps({"kind": kind}), "installation_token": "t"}
    assert provisioning.transport_kind(options) is None
    assert provisioning.access_from_options(options, {}) is None
    assert provisioning.watched_addon_slugs(options) == {HEIZUNGSBRUECKE_ADDON_SLUG, CLOUDFLARED_ADDON_SLUG}
