"""Connector registry. Connectors are imported lazily by dotted path."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module

import httpx

from connectors.base import Connector, ConnectorError


@dataclass(frozen=True)
class Spec:
    path: str  # "package.module:ClassName"
    required: Mapping[str, str]  # constructor kwarg -> required env var
    optional: Mapping[str, str] = field(default_factory=dict)  # kwarg -> optional env var


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
    module_name, _, cls_name = spec.path.partition(":")
    cls = getattr(import_module(module_name), cls_name)
    kwargs = {kw: environ[var] for kw, var in spec.required.items()}
    kwargs.update({kw: environ[var] for kw, var in spec.optional.items() if environ.get(var)})
    if client is not None:
        kwargs["client"] = client
    return cls(**kwargs)


def available(environ: Mapping[str, str] = os.environ) -> dict[str, Connector]:
    """Connectors whose required env vars are all set (others are not imported)."""
    return {
        name: get(name, environ)
        for name, spec in SPECS.items()
        if not _missing(spec, environ)
    }
