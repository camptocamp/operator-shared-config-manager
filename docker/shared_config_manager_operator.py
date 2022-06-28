#!/usr/bin/env python3

import asyncio
import logging
import os
from typing import Any, Dict

import kopf
import kopf._cogs.structs.bodies
import kopf._core.actions.execution
import kubernetes  # type: ignore
import yaml

LOCK: asyncio.Lock

ENVIRONMENT: str = os.environ["ENVIRONMENT"]

sharedconfigconfigs: Dict[str, kopf._cogs.structs.bodies.Body] = {}
sharedconfigsources: Dict[str, kopf._cogs.structs.bodies.Body] = {}


@kopf.on.startup()
async def startup(settings: kopf.OperatorSettings, **_) -> None:
    settings.posting.level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
    if "KOPF_SERVER_TIMEOUT" in os.environ:
        settings.watching.server_timeout = int(os.environ["KOPF_SERVER_TIMEOUT"])
    if "KOPF_CLIENT_TIMEOUT" in os.environ:
        settings.watching.client_timeout = int(os.environ["KOPF_CLIENT_TIMEOUT"])
    global LOCK  # pylint: disable=global-statement
    LOCK = asyncio.Lock()


@kopf.on.resume("camptocamp.com", "v2", "sharedconfigconfigs")
@kopf.on.create("camptocamp.com", "v2", "sharedconfigconfigs")
@kopf.on.update("camptocamp.com", "v2", "sharedconfigconfigs")
async def config_kopf(
    body: kopf._cogs.structs.bodies.Body,
    meta: kopf._cogs.structs.bodies.Meta,
    spec: kopf._cogs.structs.bodies.Spec,
    logger: kopf._cogs.helpers.typedefs.Logger,
    **_,
) -> None:
    if spec["environment"] != ENVIRONMENT:
        return
    sharedconfigconfigs[meta["name"]] = body
    await update_config(body, logger)


@kopf.on.delete("camptocamp.com", "v2", "sharedconfigconfigs")
async def delete_config(
    meta: kopf._cogs.structs.bodies.Meta,
    spec: kopf._cogs.structs.bodies.Spec,
    logger: kopf._cogs.helpers.typedefs.Logger,
    **_,
) -> None:
    if spec["environment"] != ENVIRONMENT:
        return
    if meta["name"] in sharedconfigconfigs:
        del sharedconfigconfigs[meta["name"]]
    logger.info(
        "Delete config %s.%s (%s).",
        meta["namespace"],
        meta["name"],
        spec["matchLabels"],
    )


@kopf.on.resume("camptocamp.com", "v2", "sharedconfigsources")
@kopf.on.create("camptocamp.com", "v2", "sharedconfigsources")
@kopf.on.update("camptocamp.com", "v2", "sharedconfigsources")
async def source_kopf(
    body: kopf._cogs.structs.bodies.Body,
    meta: kopf._cogs.structs.bodies.Meta,
    spec: kopf._cogs.structs.bodies.Spec,
    logger: kopf._cogs.helpers.typedefs.Logger,
    **_,
) -> None:
    if spec["environment"] != ENVIRONMENT:
        return
    sharedconfigsources[meta["name"]] = body
    await update_source(body, logger)


@kopf.on.delete("camptocamp.com", "v2", "sharedconfigsources")
async def delete_source(
    body: kopf._cogs.structs.bodies.Body,
    meta: kopf._cogs.structs.bodies.Meta,
    spec: kopf._cogs.structs.bodies.Spec,
    logger: kopf._cogs.helpers.typedefs.Logger,
    **_,
) -> None:
    if spec["environment"] != ENVIRONMENT:
        return
    if meta["name"] in sharedconfigsources:
        del sharedconfigsources[meta["name"]]
    await update_source(body, logger)


def match(source: kopf._cogs.structs.bodies.Body, config: kopf._cogs.structs.bodies.Body) -> bool:
    """
    Check if the source labels matches the config matchLables.
    """
    for label, value in config.spec["matchLabels"].items():
        if label not in source.meta.labels:
            return False
        if source.meta.labels[label] != value:
            return False
    return True


async def update_source(
    source: kopf._cogs.structs.bodies.Body, logger: kopf._cogs.helpers.typedefs.Logger
) -> None:
    for config in sharedconfigconfigs.values():
        if match(source, config):
            await update_config(config, logger)


async def update_config(
    config: kopf._cogs.structs.bodies.Body, logger: kopf._cogs.helpers.typedefs.Logger
) -> None:
    global LOCK  # pylint: disable=global-variable-not-assigned
    async with LOCK:
        configmap_content: Dict[str, Any] = {config.spec["property"]: {}}
        for source in sharedconfigsources.values():
            if match(source, config):
                kopf.event(
                    source,
                    type="SharedConfigManager",
                    reason="Used",
                    message="Used by SharedConfigConfig " f"{config.meta.namespace}:{config.meta.name}",
                )
                kopf.event(
                    config,
                    type="SharedConfigManager",
                    reason="Use",
                    message=f"Use SharedConfigSource {source.meta.namespace}:{source.meta.name}",
                )

                configmap_content[config.spec["property"]][source.spec["name"]] = source.spec["content"]

        logger.info(
            "Create or update ConfigMap %s.%s (%s).",
            config.meta.namespace,
            config.meta.name,
            config.spec["matchLabels"],
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
