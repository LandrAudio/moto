import hashlib
import io
import os
import zipfile
from typing import Any
from unittest import mock

from moto.awslambda import models

CODE_DIGEST = "ab" * 32
LAYER_ARN = "arn:aws:lambda:us-east-1:123456789012:layer:deps:1"


def scoped(network: str, name: str) -> str:
    return hashlib.sha256(f"{network}/{name}".encode()).hexdigest()


class FakeVolume:
    def __init__(self, name: str):
        self.name = name

    def remove(self) -> None:
        pass


class FakeVolumes:
    """docker_client.volumes: the volumes already on the daemon, and the ones created"""

    def __init__(self, existing: list[str]):
        self.existing = [FakeVolume(name) for name in existing]
        self.created: list[str] = []

    def list(self) -> list[FakeVolume]:
        return self.existing

    def create(self, name: str) -> FakeVolume:
        self.created.append(name)
        return FakeVolume(name)


def a_zip() -> bytes:
    code = io.BytesIO()
    with zipfile.ZipFile(code, "w") as archive:
        archive.writestr("handler.py", "")
    return code.getvalue()


def a_function(volumes: FakeVolumes, layers: list[dict[str, str]]) -> Any:
    return mock.Mock(
        code_digest=CODE_DIGEST,
        code_bytes=a_zip(),
        docker_client=mock.Mock(volumes=volumes),
        layers=layers,
        account_id="123456789012",
        region="us-east-1",
    )


def network(name: str | None) -> Any:
    environ = {k: v for k, v in os.environ.items() if k != "MOTO_DOCKER_NETWORK_NAME"}
    if name:
        environ["MOTO_DOCKER_NETWORK_NAME"] = name
    return mock.patch.dict(os.environ, environ, clear=True)


def code_volume(net: str | None, existing: list[str]) -> tuple[str, list[str]]:
    volumes = FakeVolumes(existing)
    with network(net):
        with models._DockerDataVolumeContext(a_function(volumes, [])) as context:
            return context.name, volumes.created


def layer_volume(net: str | None, existing: list[str]) -> tuple[str, list[str]]:
    volumes = FakeVolumes(existing)
    backend = mock.Mock()
    backend.layers_versions_by_arn.return_value.code_bytes = a_zip()
    backends = {"123456789012": {"us-east-1": backend}}
    function = a_function(volumes, [{"Arn": LAYER_ARN}])
    with network(net), mock.patch.object(models, "lambda_backends", backends):
        with models._DockerDataVolumeLayerContext(function) as context:
            return context.name, volumes.created


def test_code_volume_is_named_after_the_code_without_a_network():
    assert code_volume(None, []) == (CODE_DIGEST, [CODE_DIGEST])


def test_code_volumes_of_two_servers_on_their_own_networks_differ():
    first, _ = code_volume("stack1_default", [])
    second, _ = code_volume("stack2_default", [])
    assert first == scoped("stack1_default", CODE_DIGEST)
    assert second == scoped("stack2_default", CODE_DIGEST)


def test_another_servers_code_volume_is_not_adopted():
    # Other servers' volumes for the same code: without a network, and on another network
    others = [CODE_DIGEST, scoped("stack2_default", CODE_DIGEST)]
    name, created = code_volume("stack1_default", others)
    assert name == scoped("stack1_default", CODE_DIGEST)
    assert created == [name]


def test_its_own_code_volume_is_reused():
    own = scoped("stack1_default", CODE_DIGEST)
    assert code_volume("stack1_default", [CODE_DIGEST, own]) == (own, [])


def test_layer_volume_is_scoped_by_the_network():
    name, created = layer_volume("stack1_default", ["deps_1"])
    assert name == scoped("stack1_default", "deps_1")
    assert created == [name]


def test_layer_volume_is_named_after_the_layers_without_a_network():
    assert layer_volume(None, []) == ("deps_1", ["deps_1"])
    assert models._docker_volume_name("") == ""
