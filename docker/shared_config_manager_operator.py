#!/usr/bin/env python3

import asyncio
import logging
import os
import re
from typing import Any, Optional

import kopf
import kubernetes  # type: ignore
import yaml

_LOCK: asyncio.Lock

_ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "")
_INTERVAL = float(os.environ.get("INTERVAL", "10"))

_CHANGED_CONFIGS: list[tuple[str, str]] = []

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]*$")
_GO_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_source(source: kopf.Body) -> bool:
    """
    Validate the source spec.
    """

    if "name" in source.spec:
        if not isinstance(source.spec["name"], str):
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=(
                    "The source name must be a string. "
                    f"Got {source['name']} of type {type(source['name'])}."
                ),
            )
            return False
        if not _NAME_RE.match(source.spec["name"]):
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=(
                    "The source name must match the regular expression "
                    f"{_NAME_RE.pattern}. Got {source['name']}."
                ),
            )
            return False

    for var, secret in source.spec.get("external_secret", {}).items():
        if not isinstance(secret, str):
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=f"The external secret must be a string. Got {secret} of type {type(secret)}.",
            )
            return False
        if not _NAME_RE.match(secret):
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=(
                    "The external secret must match the regular expression "
                    f"{_NAME_RE.pattern}. Got {secret}."
                ),
            )
            return False
        if not _GO_NAME_RE.match(var):
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=(
                    "The external secret variable name must match the regular expression "
                    f"{_GO_NAME_RE.pattern}. Got {var}."
                ),
            )
            return False
    return True


@kopf.on.startup()
async def startup(settings: kopf.OperatorSettings, logger: kopf.Logger, **_) -> None:
    """Startup the operator."""
    settings.posting.level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))

    if "KOPF_SERVER_TIMEOUT" in os.environ:
        settings.watching.server_timeout = int(os.environ["KOPF_SERVER_TIMEOUT"])
    if "KOPF_CLIENT_TIMEOUT" in os.environ:
        settings.watching.client_timeout = int(os.environ["KOPF_CLIENT_TIMEOUT"])
    global _LOCK  # pylint: disable=global-statement
    _LOCK = asyncio.Lock()
    logger.info("Startup in environment %s", _ENVIRONMENT)


@kopf.index("camptocamp.com", "v4", f"sharedconfigconfigs{_ENVIRONMENT}")
async def shared_config_configs(
    body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **_
) -> dict[None, kopf.Body]:
    """Index the configs."""
    logger.info("Index config, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    global _LOCK  # pylint: disable=global-variable-not-assigned
    async with _LOCK:
        _CHANGED_CONFIGS.append((meta["namespace"], meta["name"]))
    return {None: body}


@kopf.index("camptocamp.com", "v4", f"sharedconfigsources{_ENVIRONMENT}")
async def shared_config_sources(
    body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **kwargs
) -> dict[None, kopf.Body]:
    """Index the sources."""
    logger.info("Index source, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    await _fill_changed_configs(body, **kwargs)
    return {None: body}


@kopf.on.delete("camptocamp.com", "v4", f"sharedconfigsources{_ENVIRONMENT}")
async def on_source_deleted(body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **kwargs) -> None:
    """Apply the config when a source is deleted."""
    logger.info(
        "Delete source, name: %s, namespace: %s",
        meta.get("name"),
        meta.get("namespace"),
    )
    await _fill_changed_configs(body, **kwargs)


async def _fill_changed_configs(source: kopf.Body, shared_config_configs: kopf.Index, **_):  # pylint: disable=redefined-outer-name
    global _LOCK  # pylint: disable=global-variable-not-assigned
    async with _LOCK:
        for config in shared_config_configs.get(None, []):
            assert isinstance(config, kopf.Body)
            if _match(source, config):
                _CHANGED_CONFIGS.append((config.metadata["namespace"], config.metadata["name"]))


@kopf.daemon(
    "camptocamp.com",
    "v4",
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
    """
    Daemon to update the config.
    """
    logger.info("Timer config, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    global _LOCK, _CHANGED_CONFIGS  # pylint: disable=global-variable-not-assigned

    while not stopped:
        async with _LOCK:
            result = None
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
    shared_config_sources: kopf.Index,  # pylint: disable=redefined-outer-name
    logger: kopf.Logger,
    **_,
) -> Optional[list[list[str]]]:
    content: dict[str, Any] = {config.spec["property"]: {}}
    external_secrets_data: list[dict[str, Any]] = []
    gen_external_secret: bool = config.spec.get("outputKind", "ConfigMap") == "ExternalSecret"
    sources: set[tuple[str, str, str]] = set()
    for source in shared_config_sources.get(None, []):
        try:
            assert isinstance(source, kopf.Body)
            if not _validate_source(source):
                continue
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
                if gen_external_secret:
                    namespace = source.meta.namespace if source.meta.namespace else "unknown-namespace"
                    namespace_no_dash = namespace.replace("-", "_")
                    external_secrets_data.extend(
                        [
                            {
                                "secretKey": f"{namespace_no_dash}_{key}",
                                "remoteRef": {
                                    "key": f"{config.spec.get('externalSecretPrefix')}-{namespace}-{value}"
                                },
                            }
                            for key, value in source.spec.get("external_secret", {}).items()
                        ]
                    )
                    template_data = {
                        key: f"{{{{ .{namespace_no_dash}_{key} }}}}"
                        for key in source.spec.get("external_secret", {}).keys()
                    }
                    try:
                        content[config.spec["property"]][source.spec["name"]] = yaml.load(
                            yaml.dump(source.spec["content"], Dumper=yaml.SafeDumper)
                            .replace("{{", "{{{{`{{{{`}}}}")
                            .format(**template_data),
                            Loader=yaml.SafeLoader,
                        )
                    except (KeyError, ValueError) as exception:
                        content = source.spec["content"]
                        data = yaml.dump(template_data, Dumper=yaml.SafeDumper)
                        logger.error(
                            "Error while processing source %s.%s, unable to format content:\n%s\nwith:\n%s\nerror:%s",
                            source.meta.namespace,
                            source.meta.name,
                            content,
                            data,
                            exception,
                        )
                        kopf.event(
                            source,
                            type="SharedConfigOperator",
                            reason="Error",
                            message=f"Error while processing source, unable to format content:\n{content}\nwith:\n{data}\nerror:{exception}",
                        )

                else:
                    content[config.spec["property"]][source.spec["name"]] = source.spec["content"]
        except Exception as exception:
            logger.error(
                "Error while processing source %s.%s: %s", source.meta.namespace, source.meta.name, exception
            )
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=f"Error while processing source: {exception}",
            )
            raise

    if status is None or {tuple(source) for source in status} != sources:
        output_kind = config.spec.get("outputKind", "ConfigMap")
        logger.info(
            "Create or update %s %s.%s (%s), labels: %s, sources: %s.",
            output_kind,
            config.meta.namespace,
            config.meta.name,
            config.spec["matchLabels"],
            ", ".join(content[config.spec["property"]].keys()),
            ", ".join([":".join(e) for e in sources]),
        )
        match output_kind:
            case "ConfigMap":
                config_map = {
                    "data": {
                        config.spec["configmapName"]: yaml.dump(
                            content, default_flow_style=False, Dumper=yaml.SafeDumper
                        )
                    },
                }

                api = kubernetes.client.CoreV1Api()

                current_config_map = None
                try:
                    current_config_map = api.read_namespaced_config_map(
                        namespace=config.meta.namespace,
                        name=config.meta.name,
                    )
                except kubernetes.client.exceptions.ApiException as exception:
                    if exception.status != 404:
                        raise

                if current_config_map:
                    api.patch_namespaced_config_map(
                        namespace=config.meta.namespace,
                        name=config.meta.name,
                        body=config_map,
                    )
                else:
                    config_map_full = {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {
                            "name": config.meta.name,
                        },
                        **config_map,
                    }
                    kopf.adopt(config_map_full, config)
                    api.create_namespaced_config_map(namespace=config.meta.namespace, body=config_map_full)

            case "ExternalSecret":
                external_secret = {
                    "spec": {
                        "refreshInterval": config.spec.get("refreshInterval", "1h"),
                        "secretStoreRef": config.spec["secretStoreRef"],
                        "data": external_secrets_data,
                        "target": {
                            "name": config.meta.name,
                            "template": {
                                "data": {
                                    config.spec["configmapName"]: yaml.dump(
                                        content, default_flow_style=False, Dumper=yaml.SafeDumper
                                    )
                                },
                            },
                        },
                    },
                }

                api = kubernetes.client.CustomObjectsApi()
                current_external_secret = None
                try:
                    current_external_secret = api.get_namespaced_custom_object(
                        group="external-secrets.io",
                        version="v1beta1",
                        plural="externalsecrets",
                        namespace=config.meta.namespace,
                        name=config.meta.name,
                    )
                except kubernetes.client.exceptions.ApiException as exception:
                    if exception.status != 404:
                        raise

                if current_external_secret:
                    api.patch_namespaced_custom_object(
                        group="external-secrets.io",
                        version="v1beta1",
                        plural="externalsecrets",
                        namespace=config.meta.namespace,
                        name=config.meta.name,
                        body=external_secret,
                    )
                else:
                    external_secret_full = {
                        "apiVersion": "external-secrets.io/v1beta1",
                        "kind": "ExternalSecret",
                        "metadata": {
                            "name": config.meta.name,
                        },
                        **external_secret,
                    }
                    kopf.adopt(external_secret_full, config)

                    api.create_namespaced_custom_object(
                        group="external-secrets.io",
                        version="v1beta1",
                        plural="externalsecrets",
                        namespace=config.meta.namespace,
                        body=external_secret_full,
                    )

                return [list(s) for s in sources]
            case _:
                logger.error("Unknown outputKind %s", config.spec.get("outputKind", "ConfigMap"))

    return None
