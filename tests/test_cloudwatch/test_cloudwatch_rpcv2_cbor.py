from datetime import datetime, timezone
from unittest import SkipTest, mock

import boto3
import botocore.model
import pytest
import requests
from botocore.exceptions import ClientError

from moto import mock_aws, settings
from moto.core.cbor import CborTag, cbor_decode, cbor_encode


@pytest.fixture(name="cloudwatch")
def fixture_cloudwatch():
    """A client that speaks Smithy RPCv2 CBOR, as the Go and Java SDKs do for CloudWatch"""
    protocols = ("smithy-rpc-v2-cbor",) + tuple(
        botocore.model.PRIORITY_ORDERED_SUPPORTED_PROTOCOLS
    )
    with mock.patch.object(
        botocore.model, "PRIORITY_ORDERED_SUPPORTED_PROTOCOLS", protocols
    ):
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            sent = []
            client.meta.events.register(
                "before-send", lambda request, **_: sent.append(request)
            )
            client.sent = sent  # type: ignore[attr-defined]
            yield client


def put_alarm(client, name="alarm"):
    client.put_metric_alarm(
        AlarmName=name,
        MetricName="CPUUtilization",
        Namespace="AWS/EC2",
        Statistic="Average",
        Period=60,
        EvaluationPeriods=2,
        Threshold=80.5,
        ComparisonOperator="GreaterThanThreshold",
        Tags=[{"Key": "team", "Value": "kit"}],
    )


def test_the_client_sends_cbor(cloudwatch):
    put_alarm(cloudwatch)
    request = cloudwatch.sent[0]
    assert request.url.endswith(
        "/service/GraniteServiceVersion20100801/operation/PutMetricAlarm"
    )
    content_type = request.headers["Content-Type"]
    assert content_type in (b"application/cbor", "application/cbor")


def test_alarm_lifecycle(cloudwatch):
    put_alarm(cloudwatch)

    alarm = cloudwatch.describe_alarms(AlarmNames=["alarm"])["MetricAlarms"][0]
    assert alarm["Threshold"] == 80.5
    assert alarm["EvaluationPeriods"] == 2
    assert isinstance(alarm["StateUpdatedTimestamp"], datetime)
    tags = cloudwatch.list_tags_for_resource(ResourceARN=alarm["AlarmArn"])["Tags"]
    assert tags == [{"Key": "team", "Value": "kit"}]

    cloudwatch.delete_alarms(AlarmNames=["alarm"])
    assert cloudwatch.describe_alarms()["MetricAlarms"] == []


def test_metric_data_round_trips(cloudwatch):
    when = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    cloudwatch.put_metric_data(
        Namespace="kit",
        MetricData=[{"MetricName": "uploads", "Timestamp": when, "Value": 3.0}],
    )

    stats = cloudwatch.get_metric_statistics(
        Namespace="kit",
        MetricName="uploads",
        StartTime=datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc),
        EndTime=datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc),
        Period=3600,
        Statistics=["Sum"],
    )
    assert [d["Sum"] for d in stats["Datapoints"]] == [3.0]


def test_an_error_reaches_the_client_with_its_code(cloudwatch):
    with pytest.raises(ClientError) as exc:
        cloudwatch.get_dashboard(DashboardName="unknown")
    assert exc.value.response["Error"]["Code"] == "ResourceNotFound"
    assert exc.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_a_raw_cbor_request_to_the_server():
    """As the Go SDK (Terraform's AWS provider) sends it"""
    if settings.TEST_SERVER_MODE:
        raise SkipTest("Starts its own server")
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        url = f"http://127.0.0.1:{server._server.port}"  # type: ignore[union-attr]
        headers = {
            "Content-Type": "application/cbor",
            "Accept": "application/cbor",
            "smithy-protocol": "rpc-v2-cbor",
            "Authorization": "AWS4-HMAC-SHA256 Credential=ak/20261111/us-east-1/monitoring/aws4_request, SignedHeaders=host, Signature=x",
        }
        operation = f"{url}/service/GraniteServiceVersion20100801/operation"
        put = requests.post(
            f"{operation}/PutMetricAlarm",
            data=cbor_encode(
                {
                    "AlarmName": "raw",
                    "MetricName": "m",
                    "Namespace": "n",
                    "Statistic": "Sum",
                    "Period": 60,
                    "EvaluationPeriods": 1,
                    "Threshold": 1.0,
                    "ComparisonOperator": "GreaterThanThreshold",
                }
            ),
            headers=headers,
        )
        assert put.status_code == 200, put.content
        assert put.headers["smithy-protocol"] == "rpc-v2-cbor"

        described = requests.post(
            f"{operation}/DescribeAlarms", data=cbor_encode({}), headers=headers
        )
        assert described.headers["Content-Type"] == "application/cbor"
        alarm = cbor_decode(described.content)["MetricAlarms"][0]
        assert alarm["AlarmName"] == "raw"
        assert isinstance(alarm["StateUpdatedTimestamp"], CborTag)
        assert alarm["StateUpdatedTimestamp"].tag == 1

        missing = requests.post(
            f"{operation}/GetDashboard",
            data=cbor_encode({"DashboardName": "unknown"}),
            headers=headers,
        )
        error = cbor_decode(missing.content)
        assert missing.status_code == 404
        assert error["__type"] == "com.amazonaws.cloudwatch#ResourceNotFound"
        assert error["message"]
    finally:
        server.stop()
