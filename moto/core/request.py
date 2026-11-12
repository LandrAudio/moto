from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from werkzeug.wrappers import Request

from moto.core.utils import gzip_decompress
from moto.settings import MAX_FORM_MEMORY_SIZE
from moto.utilities.constants import APPLICATION_JSON, JSON_TYPES

if TYPE_CHECKING:
    from botocore.awsrequest import AWSPreparedRequest

    from moto.core.model import ServiceModel


def normalize_request(request: AWSPreparedRequest | Request) -> Request:
    if isinstance(request, Request):
        return request
    body = request.body
    # Request.from_values() does not automatically handle gzip-encoded bodies,
    # like the full WSGI server would, so we need to do it manually.
    if request.headers.get("Content-Encoding") == "gzip":
        body = gzip_decompress(body)  # type: ignore[arg-type]
    parsed_url = urlparse(request.url)
    Request.max_form_memory_size = MAX_FORM_MEMORY_SIZE
    normalized_request = Request.from_values(
        method=request.method,
        base_url=f"{parsed_url.scheme}://{parsed_url.netloc}",
        path=parsed_url.path,
        query_string=parsed_url.query,
        data=body,
        headers=[(k, v) for k, v in request.headers.items()],
    )
    return normalized_request


def is_rpc_v2_cbor(headers: Any) -> bool:
    """Smithy RPCv2 CBOR: every request carries the smithy-protocol header (even one without a body)"""
    # Header names are case-insensitive, but headers may come as a plain dict (Smithy-Protocol from werkzeug)
    value = next(
        (v for k, v in headers.items() if str(k).lower() == "smithy-protocol"), ""
    )
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    return value == "rpc-v2-cbor"


def determine_request_protocol(
    service_model: ServiceModel,
    content_type: str | None = None,
    rpc_v2_cbor: bool = False,
) -> str:
    protocol = str(service_model.protocol)
    # Short circuit protocol detection for S3 because the ContentType header
    # is often set based on the MIME type of the object data being uploaded.
    if service_model.service_name == "s3":
        return protocol
    supported_protocols = service_model.metadata.get("protocols", [protocol])
    if rpc_v2_cbor and "smithy-rpc-v2-cbor" in supported_protocols:
        return "smithy-rpc-v2-cbor"
    content_type = content_type if content_type is not None else ""
    if content_type in JSON_TYPES:
        protocol = "rest-json" if content_type == APPLICATION_JSON else "json"
    elif content_type.startswith("application/x-www-form-urlencoded"):
        protocol = "ec2" if "ec2" in supported_protocols else "query"
    if protocol not in supported_protocols:
        raise NotImplementedError(
            f"Unsupported protocol [{protocol}] for service {service_model.service_name}"
        )
    return protocol
