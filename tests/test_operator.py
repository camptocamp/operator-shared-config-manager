import datetime
import json
import logging
import os.path
import subprocess
import time

import pytest
import yaml

LOG = logging.getLogger(__name__)


@pytest.fixture
def install_operator(scope="session"):
    with open("operator.yaml", "w") as operator_file:
        subprocess.run(
            [
                "helm",
                "template",
                "test",
                "--namespace=default",
                "--set=image.tag=latest",
                '--set-json=args=["--debug"]',
                "--set=env.ENVIRONMENT=test,env.INTERVAL=0.2,crd.suffix=test,crd.shortSuffix=t",
                ".",
            ],
            stdout=operator_file,
            check=True,
        )
    subprocess.run(["kubectl", "apply", "--filename=operator.yaml"], check=True)
    subprocess.run(["kubectl", "create", "namespace", "source"], check=True)
    subprocess.run(["kubectl", "create", "namespace", "config"], check=True)

    pods = []
    success = False
    for _ in range(100):
        pods = json.loads(
            subprocess.run(
                ["kubectl", "get", "pods", "--output=json"], check=True, stdout=subprocess.PIPE,
            ).stdout,
        )
        if (
            len(pods["items"]) == 1
            and len(
                [c for c in pods["items"][0].get("status", {}).get("conditions", {}) if c["status"] != "True"],
            )
            == 0
        ):
            success = True
            break
        time.sleep(1)
    assert success, "The operator didn't run correctly: \n" + yaml.dump(pods)
    LOG.warning("Operator created: %s", datetime.datetime.now())

    pods = []
    success = False
    for _ in range(100):
        pods = json.loads(
            subprocess.run(
                ["kubectl", "get", "pods", "--output=json"], check=True, stdout=subprocess.PIPE,
            ).stdout,
        )
        if (
            len(pods["items"]) == 1
            and len([c for c in pods["items"][0]["status"]["conditions"] if c["status"] != "True"]) == 0
        ):
            success = True
            break
        time.sleep(1)
    assert success, "The operator didn't run correctly: \n" + yaml.dump(pods)

    yield
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=default"], check=True)
    subprocess.run(["kubectl", "delete", "namespace", "config"], check=True)
    subprocess.run(["kubectl", "delete", "namespace", "source"], check=True)

    # We should have the pod to be able to extract the logs
    # subprocess.run(["kubectl", "delete", "--filename=operator.yaml"], check=True)
    os.remove("operator.yaml")


_CONFIG_MAP = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "data": {
        "test.yaml": """sources:
  test:
    branch: master
    key: admin1234
    repo: git@github.com:camptocamp/test.git
    sub_dir: dir
    template_engines:
    - data:
        TEST: test
      environment_variables: true
      type: shell
    type: git
""",
    },
}
_CONFIG_MAP_EMPTY = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "data": {
        "test.yaml": "\n".join(
            [
                "sources: {}",
                "",
            ],
        ),
    },
}
_EXTERNAL_SECRET = {
    "apiVersion": "external-secrets.io/v1beta1",
    "kind": "ExternalSecret",
    "spec": {
        "data": [
            {
                "remoteRef": {
                    "conversionStrategy": "Default",
                    "decodingStrategy": "None",
                    "key": "project-config-source-secret-value",
                    "metadataPolicy": "None",
                },
                "secretKey": "source_secret_key",
            },
        ],
        "refreshInterval": "10s",
        "secretStoreRef": {"kind": "SecretStore", "name": "keyvault"},
        "target": {
            "creationPolicy": "Owner",
            "deletionPolicy": "Retain",
            "name": "test2",
            "template": {
                "data": {
                    "test.yaml": "\n".join(
                        [
                            "sources:",
                            "  test:",
                            "    branch: master",
                            "    key: admin12341",
                            "    repo: git@github.com:camptocamp/test.git",
                            "    sub_dir: dir",
                            "    template_engines:",
                            "    - data:",
                            "        SECRET: '{{ .source_secret_key }}'",
                            "        TEST: test {{`{{`}}",
                            "      environment_variables: true",
                            "      type: shell",
                            "    type: git",
                            "",
                        ],
                    ),
                },
                "engineVersion": "v2",
                "mergePolicy": "Replace",
            },
        },
    },
}
_EXTERNAL_SECRET_EMPTY = {
    "apiVersion": "external-secrets.io/v1beta1",
    "kind": "ExternalSecret",
    "spec": {
        "data": [],
        "refreshInterval": "10s",
        "secretStoreRef": {"kind": "SecretStore", "name": "keyvault"},
        "target": {
            "creationPolicy": "Owner",
            "deletionPolicy": "Retain",
            "name": "test2",
            "template": {
                "data": {
                    "test.yaml": "\n".join(
                        [
                            "sources: {}",
                            "",
                        ],
                    ),
                },
                "engineVersion": "v2",
                "mergePolicy": "Replace",
            },
        },
    },
}

_EXTERNAL_SECRET_MIX = {
    "apiVersion": "external-secrets.io/v1beta1",
    "kind": "ExternalSecret",
    "spec": {
        "data": [
            {
                "remoteRef": {
                    "conversionStrategy": "Default",
                    "decodingStrategy": "None",
                    "key": "project-config-source-secret-value",
                    "metadataPolicy": "None",
                },
                "secretKey": "source_secret_key",
            },
        ],
        "refreshInterval": "10s",
        "secretStoreRef": {"kind": "SecretStore", "name": "keyvault"},
        "target": {
            "creationPolicy": "Owner",
            "deletionPolicy": "Retain",
            "name": "test2",
            "template": {
                "data": {
                    "test.yaml": "\n".join(
                        [
                            "sources:",
                            "  test-v3:",
                            "    branch: master",
                            "    key: admin1234",
                            "    repo: git@github.com:camptocamp/test.git",
                            "    sub_dir: dir",
                            "    template_engines:",
                            "    - data:",
                            "        TEST: test",
                            "      environment_variables: true",
                            "      type: shell",
                            "    type: git",
                            "  test-v4:",
                            "    branch: master",
                            "    key: admin12341",
                            "    repo: git@github.com:camptocamp/test.git",
                            "    sub_dir: dir",
                            "    template_engines:",
                            "    - data:",
                            "        SECRET: '{{ .source_secret_key }}'",
                            "        TEST: test {{`{{`}}",
                            "      environment_variables: true",
                            "      type: shell",
                            "    type: git",
                            "",
                        ],
                    ),
                },
                "engineVersion": "v2",
                "mergePolicy": "Replace",
            },
        },
    },
}


@pytest.mark.parametrize(
    "source_version,source_other_version,config_version,expected_type,expected_data,expected_empty",
    [
        [
            "v3",
            "v3",
            "v3",
            "configmap",
            _CONFIG_MAP,
            _CONFIG_MAP_EMPTY,
        ],
        [
            "v4",
            "v4",
            "v4",
            "externalsecret",
            _EXTERNAL_SECRET,
            _EXTERNAL_SECRET_EMPTY,
        ],
        [
            "mix",
            "v4",
            "v4",
            "externalsecret",
            _EXTERNAL_SECRET_MIX,
            _EXTERNAL_SECRET_EMPTY,
        ],
    ],
)
def test_operator(
    install_operator,
    source_version,
    source_other_version,
    config_version,
    expected_type,
    expected_data,
    expected_empty,
):
    del install_operator

    # Initialize the source and the config
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=source"], check=True)
    subprocess.run(["kubectl", "apply", f"--filename=tests/source_{source_version}.yaml"], check=True)
    subprocess.run(
        ["kubectl", "apply", f"--filename=tests/source_other_{source_other_version}.yaml"], check=True,
    )
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=config"], check=True)
    subprocess.run(["kubectl", "apply", f"--filename=tests/config_{config_version}.yaml"], check=True)

    # Wait that the Object is correctly created
    generated_object = None
    for _ in range(10):
        try:
            generated_object = json.loads(
                subprocess.run(
                    ["kubectl", "get", expected_type, "test2", "--output=json"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout,
            )
            break
        except subprocess.CalledProcessError:
            time.sleep(1)

    assert generated_object is not None, f"No {expected_type} found"
    filtered_generated_object = {k: v for k, v in generated_object.items() if k != "metadata"}
    assert filtered_generated_object == expected_data

    # Remove the source
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=source"], check=True)
    subprocess.run(["kubectl", "delete", f"--filename=tests/source_{source_version}.yaml"], check=True)

    # Wait that the Object is correctly updated
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=config"], check=True)
    filtered_generated_object = None
    for _ in range(10):
        generated_object = json.loads(
            subprocess.run(
                ["kubectl", "get", expected_type, "test2", "--output=json"],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout,
        )
        filtered_generated_object = {k: v for k, v in generated_object.items() if k != "metadata"}
        if filtered_generated_object == expected_empty:
            break
        time.sleep(1)

    assert filtered_generated_object == expected_empty

    # Remove the config
    subprocess.run(["kubectl", "delete", f"--filename=tests/config_{config_version}.yaml"], check=True)
    # Wait that the Object is correctly deleted
    success = False
    for _ in range(10):
        try:
            generated_object = json.loads(
                subprocess.run(
                    ["kubectl", "get", expected_type, "test2", "--output=json"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout,
            )
            time.sleep(1)
        except:
            success = True
            break
    assert success, "The Object is not correctly deleted"

    # Remove the other source, to be cleaned
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=source"], check=True)
    subprocess.run(
        ["kubectl", "delete", f"--filename=tests/source_other_{source_other_version}.yaml"], check=True,
    )
