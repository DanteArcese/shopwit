"""Connector registry. Connectors are imported lazily by dotted path."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module

import httpx

from connectors.base import Connector, ConnectorError
from connectors.live import LiveSearchSource, SourceInfo


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spec:
    path: str  # "package.module:ClassName"
    required: Mapping[str, str]  # constructor kwarg -> required env var
    optional: Mapping[str, str] = field(
        default_factory=dict
    )  # kwarg -> optional env var


SPECS: dict[str, Spec] = {
    "walmart": Spec(
        "connectors.walmart:WalmartConnector",
        {
            "consumer_id": "WALMART_CONSUMER_ID",
            "private_key": "WALMART_PRIVATE_KEY",
            "key_version": "WALMART_KEY_VERSION",
        },
        {"publisher_id": "WALMART_IMPACT_PUBLISHER_ID"},
    ),
}


def _missing(spec: Spec, environ: Mapping[str, str]) -> list[str]:
    return [var for var in spec.required.values() if not environ.get(var)]


def get(
    name: str,
    environ: Mapping[str, str] = os.environ,
    client: httpx.Client | None = None,
) -> Connector:
    spec = SPECS.get(name)
    if spec is None:
        raise ConnectorError(f"Unknown connector {name!r}; known: {sorted(SPECS)}")
    missing = _missing(spec, environ)
    if missing:
        raise ConnectorError(
            f"Connector {name!r} needs environment variables: {', '.join(missing)}"
        )
    try:
        module_name, _, cls_name = spec.path.partition(":")
        cls = getattr(import_module(module_name), cls_name)
        kwargs = {kw: environ[var] for kw, var in spec.required.items()}
        kwargs.update(
            {kw: environ[var] for kw, var in spec.optional.items() if environ.get(var)}
        )
        if client is not None:
            kwargs["client"] = client
        return cls(**kwargs)
    except ConnectorError as exc:
        # Our own errors carry no env values; keep the message, drop the chain.
        raise ConnectorError(f"Connector {name!r} could not be built: {exc}") from None
    except Exception:
        raise ConnectorError(f"Connector {name!r} could not be built") from None


def available(environ: Mapping[str, str] = os.environ) -> dict[str, Connector]:
    """Connectors whose required env vars are set and that construct cleanly."""
    found: dict[str, Connector] = {}
    for name, spec in SPECS.items():
        if _missing(spec, environ):
            continue
        try:
            found[name] = get(name, environ)
        except ConnectorError:
            # Name only: messages and chained causes could carry credential text.
            logger.warning("connector %s is configured but could not be built; skipping", name)
            continue
    return found


# Live sources that are not scheduled connectors, enabled by the live_off flag:
# name -> "package.module:ClassName". Task 9 adds "off-prices".
LIVE_OFF_SPECS: dict[str, str] = {}


def _class(path: str):
    module_name, _, cls_name = path.partition(":")
    return getattr(import_module(module_name), cls_name)


def _live_class(name: str, path: str):
    """The live source class, or None (logged by name only) when it cannot be read."""
    try:
        cls = _class(path)
        if not hasattr(cls, "live_search"):
            return None
        _info(name, cls)  # the attributes the web process reads must be present
        return cls
    except Exception:
        logger.warning("live source %s could not be loaded; skipping", name)
        return None


def _info(name: str, cls) -> SourceInfo:
    return SourceInfo(name=name, budget_keys=tuple(cls.budget_keys), refresh_after=cls.refresh_after)


def live_source_info(
    environ: Mapping[str, str] = os.environ, *, live_off: bool = False
) -> dict[str, SourceInfo]:
    """What ``live_sources`` would build, read from class attributes; builds no HTTP client.

    Never raises: a source that cannot be imported or lacks its attributes is skipped.
    """
    found: dict[str, SourceInfo] = {}
    for name, spec in SPECS.items():
        if _missing(spec, environ):
            continue
        cls = _live_class(name, spec.path)
        if cls is not None:
            found[name] = _info(name, cls)
    if live_off:
        for name, path in LIVE_OFF_SPECS.items():
            cls = _live_class(name, path)
            if cls is not None:
                found[name] = _info(name, cls)
    return found


def live_sources(
    environ: Mapping[str, str] = os.environ, *, live_off: bool = False
) -> dict[str, LiveSearchSource]:
    """Built live sources (worker only). Callers close them.

    Never raises: a source whose constructor fails is logged by name only and skipped.
    """
    found: dict[str, LiveSearchSource] = {}
    for name in live_source_info(environ):
        try:
            found[name] = get(name, environ)
        except Exception:  # get() already strips messages; never log one here
            logger.warning("connector %s is configured but could not be built; skipping", name)
    if live_off:
        for name, path in LIVE_OFF_SPECS.items():
            cls = _live_class(name, path)
            if cls is None:
                continue
            try:
                found[name] = cls()
            except Exception:
                logger.warning("live source %s could not be built; skipping", name)
    return found
