from fdsrouter.core.energy import EnergySettings, energy_cost


def test_from_settings_dict_parses_price_and_solar_flag():
    raw = {
        "ha_base_url": "http://homeassistant.local:8123",
        "ha_token": "secret",
        "ha_entity_id": "sensor.workstation_power",
        "electricity_price_eur_per_kwh": "0.32",
        "solar_powered": "false",
    }
    settings = EnergySettings.from_settings_dict(raw)
    assert settings.configured is True
    assert settings.electricity_price_eur_per_kwh == 0.32
    assert settings.solar_powered is False


def test_from_settings_dict_handles_missing_values():
    settings = EnergySettings.from_settings_dict({})
    assert settings.configured is False
    assert settings.electricity_price_eur_per_kwh is None
    assert settings.solar_powered is False


def test_from_settings_dict_handles_malformed_price():
    raw = {"electricity_price_eur_per_kwh": "not-a-number"}
    assert EnergySettings.from_settings_dict(raw).electricity_price_eur_per_kwh is None


def test_energy_cost_computes_from_tariff():
    settings = EnergySettings(electricity_price_eur_per_kwh=0.30, solar_powered=False)
    assert energy_cost(2.0, settings) == 0.60


def test_energy_cost_none_when_solar_powered():
    settings = EnergySettings(electricity_price_eur_per_kwh=0.30, solar_powered=True)
    assert energy_cost(2.0, settings) is None


def test_energy_cost_none_without_tariff():
    settings = EnergySettings(electricity_price_eur_per_kwh=None, solar_powered=False)
    assert energy_cost(2.0, settings) is None
