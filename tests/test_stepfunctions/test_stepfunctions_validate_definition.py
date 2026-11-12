import json

import boto3
import pytest
from botocore.exceptions import ClientError

from moto import mock_aws
from tests.test_stepfunctions.test_stepfunctions import simple_definition

HTTP_INVOKE_DEFINITION = json.dumps(
    {
        "StartAt": "CallApi",
        "States": {
            "CallApi": {
                "Type": "Task",
                "Resource": "arn:aws:states:::http:invoke",
                "Parameters": {
                    "ApiEndpoint": "https://example.com/api",
                    "Method": "GET",
                    "Authentication": {
                        "ConnectionArn": "arn:aws:events:us-east-1:123456789012:connection/api/1"
                    },
                },
                "End": True,
            }
        },
    }
)


@pytest.mark.parametrize("use_parser", [True, False], ids=["use_parser", "use_mock"])
@pytest.mark.parametrize("sm_type", ["STANDARD", "EXPRESS"])
def test_validate_state_machine_definition_ok(use_parser, sm_type):
    with mock_aws(config={"stepfunctions": {"execute_state_machine": use_parser}}):
        client = boto3.client("stepfunctions", region_name="us-east-1")

        response = client.validate_state_machine_definition(
            definition=simple_definition, type=sm_type
        )

        assert response["result"] == "OK"
        assert response["diagnostics"] == []
        assert response["truncated"] is False


@pytest.mark.parametrize("use_parser", [True, False], ids=["use_parser", "use_mock"])
@pytest.mark.parametrize(
    "definition,code",
    [
        ("{not json", "INVALID_JSON_DESCRIPTION"),
        (
            json.dumps({"States": {"Done": {"Type": "Succeed"}}}),
            "SCHEMA_VALIDATION_FAILED",
        ),
        (
            json.dumps({"StartAt": "Done", "States": {"Done": {"Type": "Unknown"}}}),
            "SCHEMA_VALIDATION_FAILED",
        ),
        (
            json.dumps(
                {"StartAt": "T", "States": {"T": {"Type": "Task", "End": True}}}
            ),
            "SCHEMA_VALIDATION_FAILED",
        ),
        (
            '{"StartAt": "A", "States": {"A": {"Type": "Pass", "Next": "A"}, "A": {"Type": "Succeed"}}}',
            "DUPLICATE_STATE_NAME",
        ),
        (
            '{"StartAt": "A", "States": {"A": {"Type": "Pass", "Result": NaN, "End": true}}}',
            "INVALID_JSON_DESCRIPTION",
        ),
    ],
    ids=[
        "invalid_json",
        "missing_start_at",
        "unknown_state_type",
        "task_without_resource",
        "duplicate_state_name",
        "nan",
    ],
)
def test_validate_state_machine_definition_fail(use_parser, definition, code):
    with mock_aws(config={"stepfunctions": {"execute_state_machine": use_parser}}):
        client = boto3.client("stepfunctions", region_name="us-east-1")

        response = client.validate_state_machine_definition(definition=definition)

        assert response["result"] == "FAIL"
        assert len(response["diagnostics"]) == 1
        diagnostic = response["diagnostics"][0]
        assert diagnostic["severity"] == "ERROR"
        assert diagnostic["code"] == code
        assert diagnostic["message"]
        assert response["truncated"] is False


@mock_aws
def test_validate_state_machine_definition_express_rejects_activities():
    client = boto3.client("stepfunctions", region_name="us-east-1")
    definition = json.dumps(
        {
            "StartAt": "Task",
            "States": {
                "Task": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:us-east-1:123456789012:activity:act",
                    "End": True,
                }
            },
        }
    )

    standard = client.validate_state_machine_definition(
        definition=definition, type="STANDARD"
    )
    assert standard["result"] == "OK"

    # Express workflows do not support activities
    express = client.validate_state_machine_definition(
        definition=definition, type="EXPRESS"
    )
    assert express["result"] == "FAIL"
    assert express["diagnostics"] == [
        {
            "severity": "ERROR",
            "code": "SCHEMA_VALIDATION_FAILED",
            "message": "Express state machine does not support Activity ARN",
        }
    ]


@mock_aws
def test_validate_state_machine_definition_integration_unknown_to_moto():
    # HTTP Tasks are valid in AWS, but Moto's parser does not know them
    client = boto3.client("stepfunctions", region_name="us-east-1")

    response = client.validate_state_machine_definition(
        definition=HTTP_INVOKE_DEFINITION
    )
    assert response["result"] == "OK"
    assert response["diagnostics"] == []

    response = client.validate_state_machine_definition(
        definition=HTTP_INVOKE_DEFINITION, severity="WARNING"
    )
    assert response["result"] == "OK"
    assert len(response["diagnostics"]) == 1
    diagnostic = response["diagnostics"][0]
    assert diagnostic["severity"] == "WARNING"
    assert diagnostic["code"] == "MOTO_UNSUPPORTED_DEFINITION"
    assert "http" in diagnostic["message"]


@mock_aws
def test_validate_state_machine_definition_max_results_zero_uses_default():
    client = boto3.client("stepfunctions", region_name="us-east-1")

    response = client.validate_state_machine_definition(
        definition="{not json", maxResults=0
    )

    assert response["result"] == "FAIL"
    assert len(response["diagnostics"]) == 1
    assert response["truncated"] is False


@mock_aws
@pytest.mark.parametrize(
    "kwargs,message",
    [
        (
            {"maxResults": 101},
            "1 validation error detected: Value '101' at 'maxResults' failed to satisfy constraint: Member must have value less than or equal to 100",
        ),
        (
            {"type": "BOGUS"},
            "1 validation error detected: Value 'BOGUS' at 'type' failed to satisfy constraint: Member must satisfy enum value set: [STANDARD, EXPRESS]",
        ),
        (
            {"severity": "INFO"},
            "1 validation error detected: Value 'INFO' at 'severity' failed to satisfy constraint: Member must satisfy enum value set: [ERROR, WARNING]",
        ),
    ],
    ids=["max_results", "type", "severity"],
)
def test_validate_state_machine_definition_invalid_parameters(kwargs, message):
    client = boto3.client("stepfunctions", region_name="us-east-1")

    with pytest.raises(ClientError) as exc:
        client.validate_state_machine_definition(definition=simple_definition, **kwargs)

    err = exc.value.response["Error"]
    assert err["Code"] == "ValidationException"
    assert err["Message"] == message
