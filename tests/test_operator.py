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
    subprocess.run(["cat", "operator.yaml"], check=True)
    subprocess.run(["kubectl", "apply", "--filename=operator.yaml"], check=True)
    subprocess.run(["kubectl", "create", "namespace", "source"], check=True)
    subprocess.run(["kubectl", "create", "namespace", "config"], check=True)

    pods = []
    success = False
    for _ in range(100):
        pods = json.loads(
            subprocess.run(
                ["kubectl", "get", "pods", "--output=json"], check=True, stdout=subprocess.PIPE
            ).stdout
        )
        if (
            len(pods["items"]) == 1
            and len(
                [c for c in pods["items"][0].get("status", {}).get("conditions", {}) if c["status"] != "True"]
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
                ["kubectl", "get", "pods", "--output=json"], check=True, stdout=subprocess.PIPE
            ).stdout
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
    # We should have the pod to be able to extract the logs
    # subprocess.run(["kubectl", "delete", "--filename=operator.yaml"], check=True)
    os.remove("operator.yaml")


def test_operator(install_operator):
    del install_operator

    # Initialize the source and the config
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=source"], check=True)
    subprocess.run(["kubectl", "apply", "--filename=tests/source.yaml"], check=True)
    subprocess.run(["kubectl", "apply", "--filename=tests/source_other.yaml"], check=True)
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=config"], check=True)
    subprocess.run(["kubectl", "apply", "--filename=tests/config.yaml"], check=True)

    # Wait that the ConfigMap is correctly created
    cm = None
    for _ in range(10):
        try:
            cm = json.loads(
                subprocess.run(
                    ["kubectl", "get", "configmap", "test2", "--output=json"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
            )
            break
        except subprocess.CalledProcessError:
            time.sleep(1)

    assert cm is not None, "No config map found"
    assert "test.yaml" in cm["data"], cm["data"].keys()
    assert (
        cm["data"]["test.yaml"]
        == """sources:
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
"""
    )

    # Remove the source
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=source"], check=True)
    subprocess.run(["kubectl", "delete", "--filename=tests/source.yaml"], check=True)

    # Wait that the ConfigMap is correctly updated
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=config"], check=True)
    data = None
    success = False
    for _ in range(10):
        try:
            cm = json.loads(
                subprocess.run(
                    ["kubectl", "get", "configmap", "test2", "--output=json"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
            )
        except:
            time.sleep(1)
        data = cm["data"]
        if data["test.yaml"].strip() == "sources: {}":
            success = True
            break
        time.sleep(1)

    assert success, data

    # Remove the config
    subprocess.run(["kubectl", "delete", "--filename=tests/config.yaml"], check=True)
    # Wait that the ConfigMap is correctly deleted
    success = False
    for _ in range(10):
        try:
            cm = json.loads(
                subprocess.run(
                    ["kubectl", "get", "configmap", "test2", "--output=json"],
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
            )
            time.sleep(1)
        except:
            success = True
            break
    assert success, "The ConfigMap is not correctly deleted"

    # Remove the other source, to be cleaned
    subprocess.run(["kubectl", "config", "set-context", "--current", "--namespace=source"], check=True)
    subprocess.run(["kubectl", "delete", "--filename=tests/source_other.yaml"], check=True)
