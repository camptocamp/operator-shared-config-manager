#!/usr/bin/env python3
# Copyright (c) 2021-2026, Camptocamp SA

import asyncio
import logging
import os
import re
from typing import Any

import kopf
import kubernetes
import yaml

_LOCK: asyncio.Lock

_ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "")
_INTERVAL = float(os.environ.get("INTERVAL", "10"))

_CHANGED_CONFIGS: list[tuple[str, str]] = []

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]*$")
_GO_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_source(source: kopf.Body) -> bool:
    """Validate the source spec."""
    if "name" in source.spec:
        if not isinstance(source.spec["name"], str):
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=(
                    f"The source name must be a string. Got {source['name']} of type {type(source['name'])}."
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
                    f"The external secret must match the regular expression {_NAME_RE.pattern}. Got {secret}."
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
async def startup(settings: kopf.OperatorSettings, logger: kopf.Logger, **_: Any) -> None:
    """Startup the operator."""
    settings.posting.level = logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))

    if "KOPF_SERVER_TIMEOUT" in os.environ:
        settings.watching.server_timeout = int(os.environ["KOPF_SERVER_TIMEOUT"])
    if "KOPF_CLIENT_TIMEOUT" in os.environ:
        settings.watching.client_timeout = int(os.environ["KOPF_CLIENT_TIMEOUT"])
    global _LOCK  # pylint: disable=global-statement # noqa: PLW0603
    _LOCK = asyncio.Lock()
    logger.info("Startup in environment %s", _ENVIRONMENT)


@kopf.index("camptocamp.com", "v4", f"sharedconfigconfigs{_ENVIRONMENT}")
async def shared_config_configs(
    body: kopf.Body,
    meta: kopf.Meta,
    logger: kopf.Logger,
    **_: Any,
) -> dict[None, kopf.Body]:
    """Index the configs."""
    logger.info("Index config, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    global _LOCK  # pylint: disable=global-variable-not-assigned # noqa: PLW0602
    async with _LOCK:
        _CHANGED_CONFIGS.append((meta["namespace"], meta["name"]))
    return {None: body}


@kopf.index("camptocamp.com", "v4", f"sharedconfigsources{_ENVIRONMENT}")
async def shared_config_sources(
    body: kopf.Body,
    meta: kopf.Meta,
    logger: kopf.Logger,
    **kwargs: Any,
) -> dict[None, kopf.Body]:
    """Index the sources."""
    logger.info("Index source, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    await _fill_changed_configs(body, **kwargs)
    return {None: body}


@kopf.on.delete("camptocamp.com", "v4", f"sharedconfigsources{_ENVIRONMENT}")
async def on_source_deleted(body: kopf.Body, meta: kopf.Meta, logger: kopf.Logger, **kwargs: Any) -> None:
    """Apply the config when a source is deleted."""
    logger.info(
        "Delete source, name: %s, namespace: %s",
        meta.get("name"),
        meta.get("namespace"),
    )
    await _fill_changed_configs(body, **kwargs)


async def _fill_changed_configs(
    source: kopf.Body,
    shared_config_configs: kopf.Index[Any, Any],  # pylint: disable=redefined-outer-name
    **_: Any,
) -> None:
    global _LOCK  # pylint: disable=global-variable-not-assigned # noqa: PLW0602
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
    **kwargs: Any,
) -> None:
    """Daemon to update the config."""
    logger.info("Timer config, name: %s, namespace: %s", meta.get("name"), meta.get("namespace"))
    global _LOCK, _CHANGED_CONFIGS  # pylint: disable=global-variable-not-assigned # noqa: PLW0602

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
    """Check if the source labels matches the config matchLables."""
    for label, value in config.spec["matchLabels"].items():
        if label not in source.meta.labels:
            return False
        if source.meta.labels[label] != value:
            return False
    return True


async def _update_config(
    config: kopf.Body,
    status: list[list[str]] | None,
    shared_config_sources: kopf.Index[Any, Any],  # pylint: disable=redefined-outer-name
    logger: kopf.Logger,
    **_: Any,
) -> list[list[str]] | None:
    content: dict[str, Any] = {config.spec["property"]: {}}
    external_secrets_data: dict[str, dict[str, Any]] = {}
    gen_external_secret: bool = config.spec.get("outputKind", "ConfigMap") == "ExternalSecret"
    namespace_prefix: bool = config.spec.get("namespacePrefix", False)
    sources: set[tuple[str, str, str]] = set()

    # Sort the sources to have a deterministic result, especially on name conflict.
    sorted_sources = sorted(
        shared_config_sources.get(None, []),
        key=lambda source: (source.meta.get("namespace") or "", source.meta.get("name") or ""),
    )

    # First pass: collect the valid and matching sources to be able to detect the name conflicts.
    matched_sources: list[kopf.Body] = []
    for source in sorted_sources:
        assert isinstance(source, kopf.Body)
        if not _validate_source(source):
            continue
        if _match(source, config):
            matched_sources.append(source)

    # Compute the starting key of each source, the one used when there is no conflict. With
    # namespacePrefix it already contains the namespace, so two sources with the same name in
    # different namespaces do not conflict. The conflict is detected on this generated key and
    # not on the raw source name, to not report artificial conflicts.
    source_starts: list[tuple[kopf.Body, str]] = []
    for source in matched_sources:
        namespace = source.meta.namespace or "<undefined>"
        start = f"{namespace}-{source.spec['name']}" if namespace_prefix else source.spec["name"]
        source_starts.append((source, start))

    start_counts: dict[str, int] = {}
    for _, start in source_starts:
        start_counts[start] = start_counts.get(start, 0) + 1

    # Compute the key used in the generated content for each source, prefixing it with the
    # namespace on conflict to not silently lose a source content.
    used_keys: set[str] = set()
    matched_with_keys: list[tuple[kopf.Body, str]] = []
    for source, start in source_starts:
        name = source.spec["name"]
        namespace = source.meta.namespace or "<undefined>"
        meta_name = source.meta.name or "<undefined>"
        has_conflict = start_counts[start] > 1
        if namespace_prefix or has_conflict:
            key = f"{namespace}-{name}"
            if key in used_keys:
                # Same namespace and same source name, fallback on the metadata name.
                key = f"{namespace}-{meta_name}-{name}"
        else:
            key = name
        used_keys.add(key)
        matched_with_keys.append((source, key))
        if has_conflict:
            others = ", ".join(
                f"{other.meta.namespace or '<undefined>'}:{other.meta.name or '<undefined>'}"
                for other, other_start in source_starts
                if other is not source and other_start == start
            )
            logger.error(
                "Conflicting source name '%s' used by config %s.%s, the source %s:%s is renamed to '%s' "
                "(also defined by %s).",
                name,
                config.meta.namespace,
                config.meta.name,
                namespace,
                meta_name,
                key,
                others,
            )
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Error",
                message=f"Conflicting source name '{name}' (also defined by {others}), renamed to '{key}'.",
            )
            kopf.event(
                config,
                type="SharedConfigOperator",
                reason="Error",
                message=(
                    f"Conflicting source name '{name}' from {namespace}:{meta_name} "
                    f"(also defined by {others}), renamed to '{key}'."
                ),
            )

    # Second pass: build the content by using the previously computed keys.
    for source, key in matched_with_keys:
        try:
            logger.debug(
                "Source %s.%s:%s used by config %s.%s",
                source.meta.namespace,
                source.meta.name,
                key,
                config.meta.namespace,
                config.meta.name,
            )
            kopf.event(
                source,
                type="SharedConfigOperator",
                reason="Used",
                message=f"Used by SharedConfigConfig {config.meta.namespace}:{config.meta.name}",
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
                ),
            )
            if gen_external_secret:
                namespace = source.meta.namespace or "unknown-namespace"
                namespace_no_dash = namespace.replace("-", "_")
                external_secrets_data.update(
                    {
                        f"{namespace_no_dash}_{secret_var}": {
                            "secretKey": f"{namespace_no_dash}_{secret_var}",
                            "remoteRef": {
                                "key": (
                                    f"{config.spec.get('externalSecretPrefix')}-{namespace}-{secret_value}"
                                ),
                            },
                        }
                        for secret_var, secret_value in source.spec.get("external_secret", {}).items()
                    },
                )
                template_data = {
                    secret_var: f"{{{{ .{namespace_no_dash}_{secret_var} }}}}"
                    for secret_var in source.spec.get("external_secret", {})
                }
                try:
                    content[config.spec["property"]][key] = yaml.load(
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
                content[config.spec["property"]][key] = source.spec["content"]
        except Exception as exception:
            logger.error(
                "Error while processing source %s.%s: %s",
                source.meta.namespace,
                source.meta.name,
                exception,
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
                            content,
                            default_flow_style=False,
                            Dumper=yaml.SafeDumper,
                        ),
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
                        "data": list(external_secrets_data.values()),
                        "target": {
                            "name": config.meta.name,
                            "template": {
                                "data": {
                                    config.spec["configmapName"]: yaml.dump(
                                        content,
                                        default_flow_style=False,
                                        Dumper=yaml.SafeDumper,
                                    ),
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
