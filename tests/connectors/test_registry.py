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


def test_available_warns_naming_connector_only(monkeypatch, caplog):
    monkeypatch.setitem(
        registry.SPECS, "boom", registry.Spec(f"{__name__}:Boom", {"token": "B_TOKEN"})
    )
    with caplog.at_level("WARNING", logger="connectors.registry"):
        assert registry.available({"B_TOKEN": "hunter2"}) == {}
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "boom" in text and "hunter2" not in text
    assert all(r.exc_info is None for r in caplog.records)


class LiveStub:
    name = "livestub"
    budget_keys = ("livestub",)
    refresh_after = __import__("datetime").timedelta(hours=3)
    built = 0

    def __init__(self, token=None, client=None):
        type(self).built += 1

    def live_search(self, phrase, limit, budget): ...


def test_live_sources_and_info(monkeypatch):
    from datetime import timedelta

    from connectors.live import SourceInfo

    assert registry.live_sources({}) == {} and registry.live_source_info({}) == {}
    monkeypatch.setitem(registry.SPECS, "stub", registry.Spec(f"{__name__}:Stub", {"token": "STUB_TOKEN"}))
    monkeypatch.setitem(registry.SPECS, "livestub", registry.Spec(f"{__name__}:LiveStub", {"token": "LIVE_TOKEN"}))
    env = {"STUB_TOKEN": "t", "LIVE_TOKEN": "t"}
    LiveStub.built = 0
    info = registry.live_source_info(env)
    assert info == {"livestub": SourceInfo("livestub", ("livestub",), timedelta(hours=3))}
    assert LiveStub.built == 0  # the web process builds no client
    built = registry.live_sources(env)
    assert set(built) == {"livestub"} and LiveStub.built == 1  # no live_search: not a live source
    assert registry.live_source_info({"STUB_TOKEN": "t"}) == {}

    monkeypatch.setattr(registry, "LIVE_OFF_SPECS", {})  # only the stubs, not the real off-prices
    monkeypatch.setitem(registry.LIVE_OFF_SPECS, "offstub", f"{__name__}:LiveStub")
    assert "offstub" not in registry.live_source_info({})
    assert set(registry.live_source_info({}, live_off=True)) == {"offstub"}
    assert set(registry.live_sources({}, live_off=True)) == {"offstub"}


class BrokenLive:
    name = "broken"
    budget_keys = ("broken",)
    refresh_after = __import__("datetime").timedelta(hours=1)

    def __init__(self, *args, **kwargs):
        raise RuntimeError("secret-token-in-message")

    def live_search(self, phrase, limit, budget): ...


class NoAttrsLive:
    def live_search(self, phrase, limit, budget): ...


def test_raising_live_source_is_skipped(monkeypatch, caplog):
    monkeypatch.setattr(registry, "LIVE_OFF_SPECS", {})
    monkeypatch.setitem(registry.LIVE_OFF_SPECS, "broken", f"{__name__}:BrokenLive")
    monkeypatch.setitem(registry.LIVE_OFF_SPECS, "noattrs", f"{__name__}:NoAttrsLive")
    monkeypatch.setitem(registry.LIVE_OFF_SPECS, "missing", "connectors.nope:Gone")
    monkeypatch.setitem(registry.LIVE_OFF_SPECS, "offstub", f"{__name__}:LiveStub")
    assert set(registry.live_source_info({}, live_off=True)) == {"broken", "offstub"}
    assert set(registry.live_sources({}, live_off=True)) == {"offstub"}
    assert "secret" not in caplog.text and "broken" in caplog.text
