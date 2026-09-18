from __future__ import annotations

from typing import Protocol

from universal_supplier.http import HttpTransport
from universal_supplier.models import DiscoveryResult, FetchRecord, ProductCard


class SupplierAdapter(Protocol):
    code: str
    base_url: str

    def discover(self, transport: HttpTransport) -> DiscoveryResult: ...
    def parse_product(self, fetch: FetchRecord) -> ProductCard: ...
