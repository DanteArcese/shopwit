import pytest

from connectors import registry


class Stub:
    name = "stub"

    def __init__(self, token, extra=None, client=None):
        self.token = token
        self.extra = extra
        self.client = client


@pytest.fixture
def stub_spec(monkeypatch):
    monkeypatch.setitem(
        registry.SPECS,
        "stub",
        registry.Spec(
            f"{__name__}:Stub",
            {"token": "STUB_TOKEN"},
            {"extra": "STUB_EXTRA"},
        ),
    )


def test_available_requires_all_env_vars():
    env = {"WALMART_CONSUMER_ID": "c", "WALMART_PRIVATE_KEY": "k"}
    assert registry.available(env) == {}  # nothing imported, key version missing


def test_available_includes_connector_when_all_set(stub_spec):
    got = registry.available({"STUB_TOKEN": "t", "STUB_EXTRA": "e"})
    assert set(got) == {"stub"}
    assert got["stub"].token == "t" and got["stub"].extra == "e"
    assert registry.available({"STUB_EXTRA": "e"}) == {}


def test_get_passes_optional_and_client(stub_spec):
    sentinel = object()
    c = registry.get("stub", {"STUB_TOKEN": "t"}, client=sentinel)
    assert c.token == "t" and c.extra is None and c.client is sentinel


def test_get_names_missing_vars_not_values(stub_spec):
    with pytest.raises(registry.ConnectorError) as exc:
        registry.get("stub", {"STUB_EXTRA": "hunter2"})
    assert "STUB_TOKEN" in str(exc.value)
    assert "hunter2" not in str(exc.value)


def test_get_treats_empty_as_unset(stub_spec):
    with pytest.raises(registry.ConnectorError, match="STUB_TOKEN"):
        registry.get("stub", {"STUB_TOKEN": ""})


def test_get_unknown_name():
    with pytest.raises(registry.ConnectorError, match="nope"):
        registry.get("nope", {})


def test_walmart_spec_registered():
    spec = registry.SPECS["walmart"]
    assert spec.path == "connectors.walmart:WalmartConnector"
    assert spec.required == {
        "consumer_id": "WALMART_CONSUMER_ID",
        "private_key": "WALMART_PRIVATE_KEY",
        "key_version": "WALMART_KEY_VERSION",
    }
    assert spec.optional == {"publisher_id": "WALMART_IMPACT_PUBLISHER_ID"}


class Boom:
    def __init__(self, token):
        raise ValueError(f"bad token {token}")


def test_constructor_error_wrapped_without_secret(monkeypatch):
    monkeypatch.setitem(
        registry.SPECS, "boom", registry.Spec(f"{__name__}:Boom", {"token": "B_TOKEN"})
    )
    with pytest.raises(registry.ConnectorError) as exc:
        registry.get("boom", {"B_TOKEN": "hunter2"})
    assert "boom" in str(exc.value) and "hunter2" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert registry.available({"B_TOKEN": "hunter2"}) == {}


def test_import_error_wrapped(monkeypatch):
    monkeypatch.setitem(
        registry.SPECS,
        "ghost",
        registry.Spec("no_such_mod_xyz:X", {"token": "G_TOKEN"}),
    )
    with pytest.raises(registry.ConnectorError, match="ghost"):
        registry.get("ghost", {"G_TOKEN": "t"})
    assert registry.available({"G_TOKEN": "t"}) == {}
