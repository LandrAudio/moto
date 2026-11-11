from .responses import CloudWatchResponse

url_bases = [r"https?://monitoring\.(.+)\.amazonaws.com"]

url_paths = {
    "{0}/$": CloudWatchResponse.dispatch,
    # Smithy RPCv2 CBOR
    "{0}/service/(?P<service>[^/]+)/operation/(?P<operation>[^/]+)$": CloudWatchResponse.dispatch,
}
