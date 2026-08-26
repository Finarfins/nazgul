"""e-Fatura seam. Default provider is NoOp — no network, inert.

``izibiz`` / ``nes`` resolve to the real adapters (Increment 1). Their unverified
wire details are isolated in :mod:`app.einvoice.endpoints` and every one of them
is marked ‹doğrulanacak›; an unconfigured endpoint fails loudly rather than
pretending to have sent anything.
"""

from .endpoints import UNCONFIGURED_ENDPOINT_ERROR, UNVERIFIED_ENDPOINT_ERROR
from .errors import ERROR_MESSAGES_TR, EInvoiceError, classify_http, message_for
from .provider import (
    EInvoiceConfiguration,
    EInvoiceProvider,
    EInvoiceResult,
    IzibizEInvoiceProvider,
    NesEInvoiceProvider,
    NoOpEInvoiceProvider,
    einvoice_configuration,
    get_einvoice_provider,
)
from .status import (
    ACCEPTED,
    FAILED,
    NONE,
    PENDING,
    QUERYABLE,
    REJECTED,
    SENT,
    TERMINAL,
    UNRESOLVED,
    advance_status,
    map_provider_status,
)
from .transport import HttpResponse, HttpTransport, TransportError
from .ubl import build_client_ettn, build_einvoice_payload, missing_required_fields, resolve_channel
from .ubl_xml import UblBuildError, build_invoice_xml, package_invoice

__all__ = [
    "ACCEPTED",
    "ERROR_MESSAGES_TR",
    "EInvoiceConfiguration",
    "EInvoiceError",
    "EInvoiceProvider",
    "EInvoiceResult",
    "FAILED",
    "HttpResponse",
    "HttpTransport",
    "IzibizEInvoiceProvider",
    "NONE",
    "NesEInvoiceProvider",
    "NoOpEInvoiceProvider",
    "PENDING",
    "QUERYABLE",
    "REJECTED",
    "SENT",
    "TERMINAL",
    "TransportError",
    "UNCONFIGURED_ENDPOINT_ERROR",
    "UNRESOLVED",
    "UNVERIFIED_ENDPOINT_ERROR",
    "UblBuildError",
    "advance_status",
    "build_client_ettn",
    "build_einvoice_payload",
    "build_invoice_xml",
    "classify_http",
    "einvoice_configuration",
    "get_einvoice_provider",
    "map_provider_status",
    "message_for",
    "missing_required_fields",
    "package_invoice",
    "resolve_channel",
]
