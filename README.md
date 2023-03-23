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

## Contributing

Install the pre-commit hooks:

```bash
pip install pre-commit
pre-commit install --allow-missing-config
```
