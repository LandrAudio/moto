import json
import time
from unittest import SkipTest

import boto3
import pytest

from moto import mock_aws, settings

ROLE = "arn:aws:iam::123456789012:role/unknown_sf_role"
DOUBLE = {
    "StartAt": "Double",
    "States": {
        "Double": {
            "Type": "Pass",
            "Parameters": {"doubled.$": "States.MathAdd($.n, $.n)"},
            "End": True,
        }
    },
}
BOOM = {
    "StartAt": "Boom",
    "States": {"Boom": {"Type": "Fail", "Error": "Boom", "Cause": "the child failed"}},
}
LONG_WAIT = {
    "StartAt": "Wait",
    "States": {"Wait": {"Type": "Wait", "Seconds": 180, "End": True}},
}


def run_child(child_arn, how, result_path, then=None):
    state = {
        "Type": "Task",
        "Resource": "arn:aws:states:::states:startExecution" + how,
        "Parameters": {"StateMachineArn": child_arn, "Input": {"n.$": "$.n"}},
        "ResultPath": result_path,
    }
    return dict(state, Next=then) if then else dict(state, End=True)


def create(client, name, definition):
    return client.create_state_machine(
        name=name, definition=json.dumps(definition), roleArn=ROLE
    )["stateMachineArn"]


def wait_for(client, execution_arn, seconds=20):
    deadline = time.monotonic() + seconds
    while True:
        described = client.describe_execution(executionArn=execution_arn)
        if described["status"] != "RUNNING" or time.monotonic() > deadline:
            return described
        time.sleep(0.2)


@pytest.fixture(name="sfn")
def fixture_sfn():
    if settings.TEST_SERVER_MODE:
        raise SkipTest("The execution engine calls Moto in-process")
    with mock_aws(config={"stepfunctions": {"execute_state_machine": True}}):
        yield boto3.client("stepfunctions", region_name="us-east-1")


def test_a_state_machine_starts_another_one_plain_sync_and_sync2(sfn):
    child = create(sfn, "child", DOUBLE)
    parent = create(
        sfn,
        "parent",
        {
            "StartAt": "Plain",
            "States": {
                "Plain": run_child(child, "", "$.plain", "Sync"),
                "Sync": run_child(child, ".sync", "$.sync", "Sync2"),
                "Sync2": run_child(child, ".sync:2", "$.sync2"),
            },
        },
    )

    execution = sfn.start_execution(stateMachineArn=parent, input='{"n":2}')
    described = wait_for(sfn, execution["executionArn"])

    assert described["status"] == "SUCCEEDED", described.get("cause")
    output = json.loads(described["output"])
    assert ":execution:child:" in output["plain"]["ExecutionArn"]
    assert isinstance(output["plain"]["StartDate"], int)
    assert output["sync"]["Status"] == "SUCCEEDED"
    assert output["sync"]["StateMachineArn"] == child
    assert json.loads(output["sync"]["Output"]) == {"doubled": 4}
    assert json.loads(output["sync"]["Input"]) == {"n": 2}
    assert output["sync2"]["Status"] == "SUCCEEDED"
    assert output["sync2"]["Output"] == {"doubled": 4}
    assert output["sync2"]["Input"] == {"n": 2}
    children = sfn.list_executions(stateMachineArn=child)["executions"]
    assert [e["status"] for e in children] == ["SUCCEEDED"] * 3


@pytest.mark.parametrize("how", [".sync", ".sync:2"])
def test_a_failing_child_fails_the_parent(sfn, how):
    child = create(sfn, "boom", BOOM)
    parent = create(
        sfn,
        "parent",
        {"StartAt": "Run", "States": {"Run": run_child(child, how, "$.run")}},
    )

    execution = sfn.start_execution(stateMachineArn=parent, input='{"n":2}')
    described = wait_for(sfn, execution["executionArn"])

    assert described["status"] == "FAILED"
    assert described["error"] == "States.TaskFailed"
    cause = json.loads(described["cause"])
    assert (cause["Status"], cause["Error"], cause["Cause"]) == (
        "FAILED",
        "Boom",
        "the child failed",
    )


def test_a_child_started_with_a_task_token_resumes_the_parent(sfn):
    child = create(sfn, "waits", LONG_WAIT)
    run = {
        "Type": "Task",
        "Resource": "arn:aws:states:::states:startExecution.waitForTaskToken",
        "Parameters": {"StateMachineArn": child, "Input": {"token.$": "$$.Task.Token"}},
        "End": True,
    }
    parent = create(sfn, "parent", {"StartAt": "Run", "States": {"Run": run}})

    execution = sfn.start_execution(stateMachineArn=parent, input="{}")
    deadline, children = time.monotonic() + 10, []
    while not children and time.monotonic() < deadline:
        time.sleep(0.1)
        children = sfn.list_executions(stateMachineArn=child)["executions"]
    assert len(children) == 1
    child_execution = children[0]["executionArn"]
    token = json.loads(sfn.describe_execution(executionArn=child_execution)["input"])[
        "token"
    ]
    sfn.send_task_success(taskToken=token, output='{"done":true}')

    described = wait_for(sfn, execution["executionArn"])
    assert described["status"] == "SUCCEEDED"
    assert json.loads(described["output"]) == {"done": True}
    sfn.stop_execution(executionArn=child_execution)
