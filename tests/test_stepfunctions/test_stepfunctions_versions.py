import json
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError

from moto import mock_aws
from tests import aws_verified
from tests.test_stepfunctions.parser import sfn_role_policy
from tests.test_stepfunctions.test_stepfunctions import simple_definition


@pytest.mark.parametrize("use_parser", [True, False], ids=["use_parser", "use_mock"])
def test_describe_state_machine_using_version_arn(use_parser):
    with mock_aws(config={"stepfunctions": {"execute_state_machine": use_parser}}):
        iam = boto3.client("iam", region_name="us-east-1")
        role_name = f"sfn_role_{str(uuid4())[0:6]}"
        sfn_role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(sfn_role_policy),
            Path="/",
        )["Role"]["Arn"]

        client = boto3.client("stepfunctions", region_name="us-east-1")

        name = f"sfn_name_{str(uuid4())[0:6]}"
        response = client.create_state_machine(
            name=name, definition=simple_definition, roleArn=sfn_role, publish=True
        )
        arn = response["stateMachineArn"]
        version_arn1 = response["stateMachineVersionArn"]

        # Use the initial version to describe the state machine
        version1 = client.describe_state_machine(stateMachineArn=version_arn1)
        assert version1["loggingConfiguration"] == {"level": "OFF"}

        # Update the state machine
        update = client.update_state_machine(
            stateMachineArn=arn,
            loggingConfiguration={"level": "ALL"},
            publish=True,
        )
        version_arn2 = update["stateMachineVersionArn"]
        assert version_arn1 != version_arn2

        # Assert that we can retrieve the latest configuration, either by the regular ARN or by the version ARN
        latest = client.describe_state_machine(stateMachineArn=arn)
        assert latest["loggingConfiguration"] == {"level": "ALL"}
        version2 = client.describe_state_machine(stateMachineArn=version_arn2)
        assert version2["loggingConfiguration"] == {"level": "ALL"}

        # Assert that we can still describe the first version of the state machine
        version1 = client.describe_state_machine(stateMachineArn=version_arn1)
        assert version1["loggingConfiguration"] == {"level": "OFF"}


@aws_verified
@pytest.mark.aws_verified
def test_create_state_machine_with_version_description():
    iam = boto3.client("iam", region_name="us-east-1")
    role_name = f"sfn_role_{str(uuid4())[0:6]}"
    sfn_role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(sfn_role_policy),
        Path="/",
    )["Role"]["Arn"]

    client = boto3.client("stepfunctions", region_name="us-east-1")

    try:
        name = f"sfn_name_{str(uuid4())[0:6]}"

        response = client.create_state_machine(
            name=name,
            definition=simple_definition,
            roleArn=sfn_role,
            versionDescription="first version",
            publish=True,
        )
        arn = response["stateMachineArn"]
        version_arn1 = response["stateMachineVersionArn"]

        # Use the initial version to describe the state machine
        sm = client.describe_state_machine(stateMachineArn=arn)
        assert "description" not in sm

        version = client.describe_state_machine(stateMachineArn=version_arn1)
        assert version["description"] == "first version"

        update = client.update_state_machine(
            stateMachineArn=arn,
            definition=simple_definition,
            versionDescription="second version",
            publish=True,
        )
        version_arn2 = update["stateMachineVersionArn"]

        version = client.describe_state_machine(stateMachineArn=version_arn2)
        assert version["description"] == "second version"
    finally:
        client.delete_state_machine(stateMachineArn=arn)
        iam.delete_role(RoleName=role_name)


@aws_verified
@pytest.mark.aws_verified
def test_create_unpublished_state_machine_with_version_description():
    iam = boto3.client("iam", region_name="us-east-1")
    role_name = f"sfn_role_{str(uuid4())[0:6]}"
    sfn_role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(sfn_role_policy),
        Path="/",
    )["Role"]["Arn"]

    client = boto3.client("stepfunctions", region_name="us-east-1")

    try:
        name = f"sfn_name_{str(uuid4())[0:6]}"

        with pytest.raises(ClientError) as exc:
            client.create_state_machine(
                name=name,
                definition=simple_definition,
                roleArn=sfn_role,
                versionDescription="first version of statemachine",
            )
        err = exc.value.response["Error"]
        assert err["Code"] == "ValidationException"
        assert (
            err["Message"] == "Version description can only be set when publish is true"
        )

    finally:
        iam.delete_role(RoleName=role_name)


@pytest.mark.parametrize("use_parser", [True, False], ids=["use_parser", "use_mock"])
def test_list_state_machine_versions(use_parser):
    with mock_aws(config={"stepfunctions": {"execute_state_machine": use_parser}}):
        client = boto3.client("stepfunctions", region_name="us-east-1")
        role = "arn:aws:iam::123456789012:role/unknown_sf_role"

        unpublished = client.create_state_machine(
            name="unpublished", definition=simple_definition, roleArn=role
        )["stateMachineArn"]
        assert (
            client.list_state_machine_versions(stateMachineArn=unpublished)[
                "stateMachineVersions"
            ]
            == []
        )

        response = client.create_state_machine(
            name="published", definition=simple_definition, roleArn=role, publish=True
        )
        arn = response["stateMachineArn"]
        version_arn1 = response["stateMachineVersionArn"]
        version_arn2 = client.update_state_machine(
            stateMachineArn=arn, definition=simple_definition, publish=True
        )["stateMachineVersionArn"]

        versions = client.list_state_machine_versions(stateMachineArn=arn)[
            "stateMachineVersions"
        ]
        assert [v["stateMachineVersionArn"] for v in versions] == [
            version_arn2,
            version_arn1,
        ]
        assert all("creationDate" in v for v in versions)

        page = client.list_state_machine_versions(stateMachineArn=arn, maxResults=1)
        assert [v["stateMachineVersionArn"] for v in page["stateMachineVersions"]] == [
            version_arn2
        ]
        page = client.list_state_machine_versions(
            stateMachineArn=arn, maxResults=1, nextToken=page["nextToken"]
        )
        assert [v["stateMachineVersionArn"] for v in page["stateMachineVersions"]] == [
            version_arn1
        ]
        assert "nextToken" not in page


@mock_aws
def test_list_state_machine_versions_of_an_unknown_state_machine():
    client = boto3.client("stepfunctions", region_name="us-east-1")
    with pytest.raises(ClientError) as exc:
        client.list_state_machine_versions(
            stateMachineArn="arn:aws:states:us-east-1:123456789012:stateMachine:unknown"
        )
    assert exc.value.response["Error"]["Code"] == "StateMachineDoesNotExist"
