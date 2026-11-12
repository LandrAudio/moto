import base64
import hashlib
import os
from unittest import SkipTest

import boto3

from moto import mock_aws, settings
from moto.awslambda.models import lambda_backends
from moto.core import DEFAULT_ACCOUNT_ID as ACCOUNT_ID

from .utilities import PYTHON_VERSION, get_role_name

_lambda_region = "us-west-2"


@mock_aws
def test_functions_from_one_zip_share_one_copy_of_it():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    conn = boto3.client("lambda", _lambda_region)
    code = os.urandom(1024 * 1024)
    for n in range(3):
        conn.create_function(
            FunctionName=f"f{n}",
            Runtime=PYTHON_VERSION,
            Role=get_role_name(),
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": code},
        )

    backend = lambda_backends[ACCOUNT_ID][_lambda_region]
    functions = [backend.get_function(f"f{n}") for n in range(3)]
    assert all(fn.code_bytes == code for fn in functions)
    assert functions[1].code_bytes is functions[0].code_bytes
    assert functions[2].code_bytes is functions[0].code_bytes
    # The request's base64 is not kept beside the decoded package
    assert all("ZipFile" not in fn.code for fn in functions)

    function = conn.get_function(FunctionName="f1")["Configuration"]
    assert function["CodeSize"] == len(code)
    assert function["CodeSha256"] == base64.b64encode(
        hashlib.sha256(code).digest()
    ).decode("utf-8")


@mock_aws
def test_updated_code_is_shared_too():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    conn = boto3.client("lambda", _lambda_region)
    first, second = os.urandom(1024), os.urandom(1024)
    for name in ("a", "b"):
        conn.create_function(
            FunctionName=name,
            Runtime=PYTHON_VERSION,
            Role=get_role_name(),
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": first},
        )
    conn.update_function_code(FunctionName="a", ZipFile=second, Publish=True)
    conn.update_function_code(FunctionName="b", ZipFile=second)

    backend = lambda_backends[ACCOUNT_ID][_lambda_region]
    a, b = backend.get_function("a"), backend.get_function("b")
    assert a.code_bytes == second
    assert a.code_bytes is b.code_bytes
    assert "ZipFile" not in a.code


@mock_aws
def test_layer_versions_from_one_zip_share_one_copy_of_it():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    conn = boto3.client("lambda", _lambda_region)
    code = os.urandom(1024)
    for n in range(2):
        conn.publish_layer_version(LayerName=f"layer{n}", Content={"ZipFile": code})

    backend = lambda_backends[ACCOUNT_ID][_lambda_region]
    layers = [backend.get_layer_version(f"layer{n}", "1") for n in range(2)]
    assert layers[0].code_bytes == code
    assert layers[0].code_bytes is layers[1].code_bytes
    assert all("ZipFile" not in layer.content for layer in layers)


def _create(conn, name: str, code: bytes) -> None:
    conn.create_function(
        FunctionName=name,
        Runtime=PYTHON_VERSION,
        Role=get_role_name(),
        Handler="lambda_function.lambda_handler",
        Code={"ZipFile": code},
    )


@mock_aws
def test_a_package_no_function_uses_is_released():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    from moto.awslambda.models import _code_store

    conn = boto3.client("lambda", _lambda_region)
    deleted, replaced, kept, new = (os.urandom(1024) for _ in range(4))
    digest = {
        code: hashlib.sha256(code).hexdigest()
        for code in (deleted, replaced, kept, new)
    }
    _create(conn, "deleted", deleted)
    _create(conn, "updated", replaced)
    _create(conn, "kept", kept)
    assert {digest[deleted], digest[replaced], digest[kept]} <= set(_code_store._held)

    conn.delete_function(FunctionName="deleted")
    conn.update_function_code(FunctionName="updated", ZipFile=new)

    assert digest[deleted] not in _code_store._held
    assert digest[replaced] not in _code_store._held
    assert {digest[kept], digest[new]} <= set(_code_store._held)


@mock_aws
def test_a_published_version_keeps_its_package():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    from moto.awslambda.models import _code_store

    conn = boto3.client("lambda", _lambda_region)
    first, second = os.urandom(1024), os.urandom(1024)
    _create(conn, "f", first)
    conn.publish_version(FunctionName="f")
    conn.update_function_code(FunctionName="f", ZipFile=second)

    # Version 1 still runs the first package
    assert hashlib.sha256(first).hexdigest() in _code_store._held
    assert hashlib.sha256(second).hexdigest() in _code_store._held


def test_packages_are_released_on_reset():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    from moto.awslambda.models import _code_store

    code = os.urandom(1024)
    with mock_aws():
        conn = boto3.client("lambda", _lambda_region)
        _create(conn, "f", code)
        conn.publish_layer_version(LayerName="layer", Content={"ZipFile": code})
        assert hashlib.sha256(code).hexdigest() in _code_store._held

    assert hashlib.sha256(code).hexdigest() not in _code_store._held


@mock_aws
def test_functions_from_one_s3_object_share_one_copy_of_it():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    s3 = boto3.client("s3", _lambda_region)
    s3.create_bucket(
        Bucket="code", CreateBucketConfiguration={"LocationConstraint": _lambda_region}
    )
    s3.put_object(Bucket="code", Key="pkg.zip", Body=os.urandom(1024))
    conn = boto3.client("lambda", _lambda_region)
    for name in ("a", "b"):
        conn.create_function(
            FunctionName=name,
            Runtime=PYTHON_VERSION,
            Role=get_role_name(),
            Handler="lambda_function.lambda_handler",
            Code={"S3Bucket": "code", "S3Key": "pkg.zip"},
        )

    backend = lambda_backends[ACCOUNT_ID][_lambda_region]
    assert backend.get_function("a").code_bytes is backend.get_function("b").code_bytes


@mock_aws(config={"lambda": {"use_docker": False}})
def test_packages_are_shared_with_the_simple_backend_too():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Reads the backend")
    from moto.awslambda_simple.models import lambda_simple_backends

    conn = boto3.client("lambda", _lambda_region)
    x, y = os.urandom(1024), os.urandom(1024)
    # Another package stored in between must not drop the first one
    _create(conn, "f1", x)
    _create(conn, "f2", y)
    _create(conn, "f3", x)

    backend = lambda_simple_backends[ACCOUNT_ID][_lambda_region]
    assert (
        backend.get_function("f3").code_bytes is backend.get_function("f1").code_bytes
    )
