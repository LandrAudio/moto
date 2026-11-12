import gc
import os
import threading
import time
import tracemalloc
from typing import Any
from unittest import SkipTest, mock

import boto3
import requests

from moto import settings
from moto.moto_server import werkzeug_app
from moto.server import ThreadedMotoServer

# Above the size from which a request counts as big (1 MiB); sizes are not read from the
# module, so that these tests reach their assertions without the feature too
CODE_BYTES = 9 * 1024 * 1024


def create_functions(n: int, before: Any = None) -> None:
    """Creates f0, then calls before(), then creates f1..fn, on a server of its own"""
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Starts its own server")
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        requests.post(f"http://{host}:{port}/moto-api/reset")
        kwargs = {
            "region_name": "us-east-1",
            "endpoint_url": f"http://{host}:{port}",
            "aws_access_key_id": "ak",
            "aws_secret_access_key": "sk",
        }
        lam = boto3.client("lambda", **kwargs)
        role = boto3.client("iam", **kwargs).create_role(
            RoleName="r", AssumeRolePolicyDocument="{}"
        )["Role"]["Arn"]
        code = os.urandom(CODE_BYTES)
        threads = threading.active_count()

        def create(name: str) -> None:
            lam.create_function(
                FunctionName=name,
                Runtime="python3.12",
                Role=role,
                Handler="h",
                Code={"ZipFile": code},
            )

        create("f0")  # the one copy of the code every function shares
        if before:
            before()
        for i in range(1, n + 1):
            create(f"f{i}")
        deadline = time.monotonic() + 10
        while threading.active_count() > threads and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        server.stop()


def test_big_requests_are_freed_once_answered() -> None:
    gc_was_enabled = gc.isenabled()
    left: list[int] = []

    def start() -> None:
        gc.collect()
        gc.disable()
        tracemalloc.start()
        left.append(tracemalloc.get_traced_memory()[0])

    try:
        # Collect after every big request
        app = werkzeug_app.DomainDispatcherApplication
        with (
            mock.patch.object(werkzeug_app, "COLLECT_AFTER_BYTES", 1, create=True),
            mock.patch.object(app, "_uncollected_bytes", 0, create=True),
        ):
            create_functions(3, before=start)
        left[0] = tracemalloc.get_traced_memory()[0] - left[0]
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        if gc_was_enabled:
            gc.enable()

    # Without the collection, each request's body (and its base64) stays until the cyclic GC
    # runs: about 21 MiB per request. The boto3 client in this process is counted too.
    assert left[0] < CODE_BYTES // 2, f"{left[0] // 1024} KB left by 3 requests"


def test_the_collector_runs_once_per_budget_of_big_requests() -> None:
    # Budget: two requests (each a bit more than CODE_BYTES, in base64 JSON) and a half
    budget = int(CODE_BYTES * 4 / 3 * 2.5)
    app = werkzeug_app.DomainDispatcherApplication
    with (
        mock.patch.object(werkzeug_app, "COLLECT_AFTER_BYTES", budget, create=True),
        mock.patch.object(app, "_uncollected_bytes", 0, create=True),
        mock.patch.object(gc, "collect") as collect,
    ):
        create_functions(5)
    # f0 and f1..f5: 6 big requests, of about 0.4 budget each
    assert collect.call_count == 2
