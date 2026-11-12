import math

import pytest

from moto.core.cbor import CborDecodeError, CborTag, cbor_decode, cbor_encode

# RFC 8949, appendix A
VECTORS = [
    (0, "00"),
    (23, "17"),
    (24, "1818"),
    (1000000, "1a000f4240"),
    (18446744073709551615, "1bffffffffffffffff"),
    (-1, "20"),
    (-1000, "3903e7"),
    (1.1, "fb3ff199999999999a"),
    (False, "f4"),
    (True, "f5"),
    (None, "f6"),
    (b"\x01\x02\x03\x04", "4401020304"),
    ("IETF", "6449455446"),
    ("ü", "62c3bc"),
    ([1, [2, 3], [4, 5]], "8301820203820405"),
    ({"a": 1, "b": [2, 3]}, "a26161016162820203"),
    (CborTag(1, 1363896240), "c11a514b67b0"),
    (CborTag(1, 1363896240.5), "c1fb41d452d9ec200000"),
]


@pytest.mark.parametrize("value,encoded", VECTORS)
def test_rfc_8949_vectors(value: object, encoded: str) -> None:
    assert cbor_encode(value).hex() == encoded
    assert cbor_decode(bytes.fromhex(encoded)) == value


@pytest.mark.parametrize(
    "encoded,value",
    [
        ("f93e00", 1.5),
        ("f9c400", -4.0),
        ("fa47c35000", 100000.0),
        ("f97c00", math.inf),
        ("fbfff0000000000000", -math.inf),
    ],
)
def test_half_single_and_double_floats_decode(encoded: str, value: float) -> None:
    assert cbor_decode(bytes.fromhex(encoded)) == value


@pytest.mark.parametrize(
    "encoded,value",
    [
        ("5f42010243030405ff", b"\x01\x02\x03\x04\x05"),
        ("7f657374726561646d696e67ff", "streaming"),
        ("9f018202039f0405ffff", [1, [2, 3], [4, 5]]),
        ("bf61610161629f0203ffff", {"a": 1, "b": [2, 3]}),
        ("a16161f7", {"a": None}),
    ],
)
def test_indefinite_lengths_and_undefined_decode(encoded: str, value: object) -> None:
    assert cbor_decode(bytes.fromhex(encoded)) == value


@pytest.mark.parametrize(
    "encoded",
    ["", "18", "62c3", "ff", "9f01", "1c", "5f6161ff", "a1800f", "0000", "c2"],
)
def test_malformed_input_is_refused(encoded: str) -> None:
    with pytest.raises(CborDecodeError):
        cbor_decode(bytes.fromhex(encoded))


def test_values_outside_cbor_are_refused() -> None:
    for value in (2**64, -(2**64) - 1, object()):
        with pytest.raises((ValueError, TypeError)):
            cbor_encode(value)


@pytest.mark.parametrize(
    "service,content_type,rpc_v2_cbor,protocol",
    [
        # Smithy RPCv2 CBOR is chosen by the smithy-protocol header, for a service that supports it
        ("cloudwatch", "application/cbor", True, "smithy-rpc-v2-cbor"),
        # An operation without input: no body, no Content-Type, the header still says it
        ("cloudwatch", None, True, "smithy-rpc-v2-cbor"),
        ("cloudwatch", "application/x-www-form-urlencoded", False, "query"),
        # A CBOR Content-Type alone is a payload's media type, not the protocol
        ("sqs", "application/cbor", False, "json"),
        # A service without the protocol keeps its own
        ("sqs", "application/cbor", True, "json"),
    ],
)
def test_the_protocol_follows_the_smithy_protocol_header(
    service: str, content_type: str | None, rpc_v2_cbor: bool, protocol: str
) -> None:
    from moto.core.request import determine_request_protocol
    from moto.core.utils import get_service_model

    model = get_service_model(service)
    assert (
        determine_request_protocol(model, content_type, rpc_v2_cbor=rpc_v2_cbor)
        == protocol
    )


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"smithy-protocol": "rpc-v2-cbor"}, True),
        ({"Smithy-Protocol": "rpc-v2-cbor"}, True),
        ({"smithy-protocol": b"rpc-v2-cbor"}, True),
        ({"Content-Type": "application/cbor"}, False),
        ({}, False),
    ],
)
def test_rpc_v2_cbor_requests_are_recognised_by_their_header(
    headers: dict[str, object], expected: bool
) -> None:
    from moto.core.request import is_rpc_v2_cbor

    assert is_rpc_v2_cbor(headers) is expected
