#!/usr/bin/env python3

import asyncio
import logging
import os
from typing import Any, Optional

import kopf
import kubernetes  # type: ignore
import yaml

_LOCK: asyncio.Lock

_ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "")
_INTERVAL = float(os.environ.get("INTERVAL", "10"))

_CHANGED_CONFIGS: list[tuple[str, str]] = []


@kopf.on.startup()
async def startup(settings: kopf.OperatorSettings, logger: kopf.Logger, **_) -> None:
    settings.posting.level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
    if "KOPF_SERVER_TIMEOUT" in os.environ:
        settings.watching.server_timeout = int(os.environ["KOPF_SERVER_TIMEOUT"])
    if "KOPF_CLIENT_TIMEOUT" in os.environ:
        settings.watching.client_timeout = int(os.environ["KOPF_CLIENT_TIMEOUT"])
    global _LOCK  # pylint: disable=global-statement
    _LOCK = asyncio.Lock()
    logger.info("Startup in environment %s", _ENVIRONMENT)


@kopf.index("camptocamp.com", "v3", f"sharedconfigconfigs{_ENVIRONMENT}")
async def sharedconfigconfigs(
    body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **_
) -> dict[None, kopf.Body]:
    logger.info("Index config, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    global _LOCK  # pylint: disable=global-variable-not-assigned
    async with _LOCK:
        _CHANGED_CONFIGS.append((meta["namespace"], meta["name"]))
    return {None: body}


@kopf.index("camptocamp.com", "v3", f"sharedconfigsources{_ENVIRONMENT}")
async def sharedconfigsources(
    body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **kwargs
) -> dict[None, kopf.Body]:
    logger.info("Index source, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    await _fill_changed_configs(body, **kwargs)
    return {None: body}


@kopf.on.delete("camptocamp.com", "v3", f"sharedconfigsources{_ENVIRONMENT}")
async def on_source_deleted(body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **kwargs) -> None:
    logger.info(
        "Delete source, name: %s, namespace: %s",
        meta.get("name"),
        meta.get("namespace"),
    )
    await _fill_changed_configs(body, **kwargs)


async def _fill_changed_configs(
    source: kopf.Body, sharedconfigconfigs: kopf.Index, **_
):  # pylint: disable=redefined-outer-name
    global _LOCK  # pylint: disable=global-variable-not-assigned
    async with _LOCK:
        for config in sharedconfigconfigs.get(None, []):
            assert isinstance(config, kopf.Body)
            if _match(source, config):
                _CHANGED_CONFIGS.append((config.metadata["namespace"], config.metadata["name"]))


@kopf.daemon(
    "camptocamp.com",
    "v3",
    f"sharedconfigconfigs{_ENVIRONMENT}",
)
async def daemon(
    stopped: kopf.DaemonStopped,
    body: kopf.Body,
    meta: kopf.Meta,
    status: kopf.Status,
    patch: kopf.Patch,
    logger: kopf.Logger,
    **kwargs,
):
    logger.info("Timer config, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    global _LOCK, _CHANGED_CONFIGS  # pylint: disable=global-variable-not-assigned

    while not stopped:
        async with _LOCK:
            if (meta["namespace"], meta["name"]) in _CHANGED_CONFIGS:
                result = await _update_config(body, status=status.get("sources"), logger=logger, **kwargs)
                _CHANGED_CONFIGS.remove((meta["namespace"], meta["name"]))
            if result is not None:
                patch.status["sources"] = result
        await asyncio.sleep(_INTERVAL)


def _match(source: kopf.Body, config: kopf.Body) -> bool:
    """
    Check if the source labels matches the config matchLables.
    """
    for label, value in config.spec["matchLabels"].items():
        if label not in source.meta.labels:
            return False
        if source.meta.labels[label] != value:
            return False
    return True


async def _update_config(
    config: kopf.Body,
    status: Optional[list[list[str]]],
    sharedconfigsources: kopf.Index,  # pylint: disable=redefined-outer-name
    logger: kopf.Logger,
    **_,
) -> Optional[list[list[str]]]:
    configmap_content: dict[str, Any] = {config.spec["property"]: {}}
    sources: set[tuple[str, str, str]] = set()
    for source in sharedconfigsources.get(None, []):
        assert isinstance(source, kopf.Body)
        if _match(source, config):
            logger.debug(
                "Source %s.%s:%s used by config %s.%s",
                source.meta.namespace,
                source.meta.name,
                source.spec["name"],
                config.meta.namespace,
                config.meta.name,
            )
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Used",
                message="Used by SharedConfigConfig " f"{config.meta.namespace}:{config.meta.name}",
            )
            kopf.event(
                config,
                type="SharedConfigOperator",
                reason="Use",
                message=f"Use SharedConfigSource {source.meta.namespace}:{source.meta.name}",
            )

            sources.add(
                (
                    source.meta.namespace or "<undefined>",
                    source.meta.name or "<undefined>",
                    source.meta.get("resourceVersion", "<undefined>"),
                )
            )
            configmap_content[config.spec["property"]][source.spec["name"]] = source.spec["content"]

    if status is None or {tuple(source) for source in status} != sources:
        logger.info(
            "Create or update ConfigMap %s.%s (%s), labels: %s, sources: %s.",
            config.meta.namespace,
            config.meta.name,
            config.spec["matchLabels"],
            ", ".join(configmap_content[config.spec["property"]].keys()),
            ", ".join([":".join(e) for e in sources]),
        )

        config_map = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "data": {
                config.spec["configmapName"]: yaml.dump(
                    configmap_content, default_flow_style=False, Dumper=yaml.SafeDumper
                )
            },
            "metadata": {
                "name": config.meta.name,
            },
        }

        # Make it our child: assign the namespace, name, labels, owner references, etc.
        kopf.adopt(config_map, config)

        api = kubernetes.client.CoreV1Api()
        try:
            api.replace_namespaced_config_map(
                name=config.meta.name, namespace=config.meta.namespace, body=config_map
            )
        except kubernetes.client.exceptions.ApiException:
            api.create_namespaced_config_map(namespace=config.meta.namespace, body=config_map)

        return [list(s) for s in sources]

    return None
