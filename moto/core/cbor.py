"""
A CBOR codec (RFC 8949) for the Smithy RPCv2 CBOR protocol:
https://smithy.io/2.0/additional-specs/protocols/smithy-rpc-v2.html
"""

import struct
from typing import Any


class CborTag:
    """A tagged CBOR value (major type 6). Tag 1: a timestamp, in epoch seconds."""

    def __init__(self, tag: int, value: Any):
        self.tag = tag
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CborTag) and (self.tag, self.value) == (
            other.tag,
            other.value,
        )

    def __repr__(self) -> str:
        return f"CborTag({self.tag}, {self.value!r})"


class CborDecodeError(ValueError):
    pass


_BREAK = object()


def _encode_head(major: int, number: int, out: bytearray) -> None:
    if number < 24:
        out.append(major << 5 | number)
        return
    for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if number < 1 << (8 * size):
            out.append(major << 5 | info)
            out += number.to_bytes(size, "big")
            return
    raise ValueError(f"CBOR: {number} does not fit in 64 bits")


def _encode(value: Any, out: bytearray) -> None:
    if value is None:
        out.append(0xF6)
    elif value is True or value is False:
        out.append(0xF5 if value else 0xF4)
    elif isinstance(value, int):
        if value >= 0:
            _encode_head(0, value, out)
        else:
            _encode_head(1, -1 - value, out)
    elif isinstance(value, float):
        out.append(0xFB)  # always a double
        out += struct.pack(">d", value)
    elif isinstance(value, str):
        data = value.encode("utf-8")
        _encode_head(3, len(data), out)
        out += data
    elif isinstance(value, (bytes, bytearray)):
        _encode_head(2, len(value), out)
        out += value
    elif isinstance(value, (list, tuple)):
        _encode_head(4, len(value), out)
        for item in value:
            _encode(item, out)
    elif isinstance(value, dict):
        _encode_head(5, len(value), out)
        for key, item in value.items():
            _encode(key, out)
            _encode(item, out)
    elif isinstance(value, CborTag):
        _encode_head(6, value.tag, out)
        _encode(value.value, out)
    else:
        raise TypeError(f"CBOR: cannot encode {type(value).__name__}")


def cbor_encode(value: Any) -> bytes:
    """None, bool, int, float, str, bytes, list, dict, CborTag -> CBOR (definite lengths)."""
    out = bytearray()
    _encode(value, out)
    return bytes(out)


def _take(data: bytes, at: int, size: int) -> tuple[bytes, int]:
    if at + size > len(data):
        raise CborDecodeError("CBOR: truncated")
    return data[at : at + size], at + size


def _pairs_to_dict(items: list[Any]) -> dict[Any, Any]:
    if len(items) % 2:
        raise CborDecodeError("CBOR: a map key without a value")
    try:
        return dict(zip(items[::2], items[1::2], strict=True))
    except TypeError as e:
        raise CborDecodeError(f"CBOR: map key: {e}")


def _decode(data: bytes, at: int) -> tuple[Any, int]:
    if at >= len(data):
        raise CborDecodeError("CBOR: truncated")
    major, info, at = data[at] >> 5, data[at] & 31, at + 1

    if major == 7:
        simple = {20: False, 21: True, 22: None, 23: None}  # 23: undefined
        if info in simple:
            return simple[info], at
        if info in (25, 26, 27):
            size = {25: 2, 26: 4, 27: 8}[info]
            raw, at = _take(data, at, size)
            return struct.unpack({2: ">e", 4: ">f", 8: ">d"}[size], raw)[0], at
        if info == 31:
            return _BREAK, at
        raise CborDecodeError(f"CBOR: simple value {info} is not supported")

    if info == 31:  # indefinite length
        items: list[Any] = []
        while True:
            if major in (2, 3) and at < len(data) and data[at] != 0xFF:
                # A string's chunks are definite strings of the same type
                if data[at] >> 5 != major or data[at] & 31 == 31:
                    raise CborDecodeError("CBOR: a chunk of another type in a string")
            item, at = _decode(data, at)
            if item is _BREAK:
                break
            items.append(item)
        if major == 2:
            return b"".join(items), at
        if major == 3:
            return "".join(items), at
        if major == 4:
            return items, at
        if major == 5:
            return _pairs_to_dict(items), at
        raise CborDecodeError(f"CBOR: major type {major} cannot be indefinite")

    if info < 24:
        number = info
    elif info <= 27:
        raw, at = _take(data, at, 1 << (info - 24))
        number = int.from_bytes(raw, "big")
    else:
        raise CborDecodeError(f"CBOR: reserved additional information {info}")

    if major == 0:
        return number, at
    if major == 1:
        return -1 - number, at
    if major == 2:
        raw, at = _take(data, at, number)
        return bytes(raw), at
    if major == 3:
        raw, at = _take(data, at, number)
        try:
            return raw.decode("utf-8"), at
        except UnicodeDecodeError as e:
            raise CborDecodeError(f"CBOR: text string: {e}")
    if major in (4, 5):
        items = []
        for _ in range(number * (2 if major == 5 else 1)):
            item, at = _decode(data, at)
            if item is _BREAK:
                raise CborDecodeError("CBOR: break inside a definite length")
            items.append(item)
        return (items if major == 4 else _pairs_to_dict(items)), at
    value, at = _decode(data, at)  # major 6: a tag
    if value is _BREAK:
        raise CborDecodeError("CBOR: break after a tag")
    return CborTag(number, value), at


def cbor_decode(data: bytes) -> Any:
    """CBOR -> Python. Raises CborDecodeError on anything malformed or left over."""
    data = bytes(data)
    value, at = _decode(data, 0)
    if value is _BREAK or at != len(data):
        raise CborDecodeError("CBOR: not exactly one data item")
    return value
