# Shared config operator

This operator can be used to build a ConfigMap that contains one yaml file, with part of config
that can come from other namespaces.

## Install

```
helm repo add operator-shared-config-manager https://camptocamp.github.io/operator-shared-config-manager/
helm install my-release operator-shared-config-manager
```

## Example

With the following [source](./tests/source.yaml) and [config](./tests/config.yaml), a Config map will be build with the same name and namespace of the config, with a file with the name as the `configmap_name` of the config, that contains:

```
<config.property>:
    <source.name>: <source-except-name>
```

with the real values:

```
sources:
  test:
    type: git
    repo: git@github.com:camptocamp/test.git
    branch: master
    key: admin1234
    sub_dir: dir
    template_engines:
      - type: shell
        environment_variables: true
        data:
          TEST: test
```

## Source name conflicts

The key used in the generated file is the `spec.name` of each source. If two sources
matching the same config share the same `spec.name` (for example one per namespace), the
key would collide and one source would silently overwrite the other.

To avoid that, when a conflict is detected all the sources of the conflicting group are
prefixed with their namespace, and an `Error` event is emitted on the sources and on the
config. With two sources named `test`, one in the namespace `ns1` and one in `ns2`:

```
sources:
  ns1-test:
    ...
  ns2-test:
    ...
```

If two conflicting sources are in the same namespace, the metadata name is also added to
the key (`<namespace>-<metadata.name>-<name>`) to keep them distinct.

## Always prefix with the namespace

Set `namespacePrefix: true` in the config `spec` to always prefix the keys with the source
namespace, even when there is no conflict:

```yaml
spec:
  matchLabels:
    app: my-app
  property: sources
  configmapName: config.yaml
  namespacePrefix: true
```

With this option the conflict is detected on the generated key (`<namespace>-<name>`) and
not on the raw `spec.name`, so two sources with the same name in different namespaces get
distinct keys and are not reported as a conflict. Only sources sharing the same name in the
same namespace still conflict.

## Contributing

Install the pre-commit hooks:

```bash
pip install pre-commit
pre-commit install --allow-missing-config
```
