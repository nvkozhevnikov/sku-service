from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PropertyValue:
    name: str
    value: str
    unit: str = ""
    position: int = 0


@dataclass(frozen=True)
class MediaItem:
    media_type: str
    url: str
    title: str = ""
    position: int = 0
    is_primary: bool = False


@dataclass(frozen=True)
class OptionValue:
    value: str
    target_url: str = ""
    variant_internal_id: str = ""
    classification: str = "INFORMATIONAL"
    selected: bool = False
    position: int = 0


@dataclass(frozen=True)
class ProductOption:
    name: str
    values: tuple[OptionValue, ...]
    position: int = 0


@dataclass(frozen=True)
class CategoryNode:
    name: str
    url: str
    position: int


@dataclass(frozen=True)
class ProductCard:
    supplier_code: str
    external_id: str
    sku: str
    name: str
    requested_url: str
    final_url: str
    canonical_url: str
    http_status: int
    redirect_urls: tuple[str, ...]
    price: Decimal | None
    old_price: Decimal | None
    currency: str
    availability_raw: str
    availability_normalized: str
    quantity: Decimal | None
    description_text: str
    description_html: str
    brand: str = ""
    manufacturer: str = ""
    properties: tuple[PropertyValue, ...] = ()
    media: tuple[MediaItem, ...] = ()
    options: tuple[ProductOption, ...] = ()
    categories: tuple[CategoryNode, ...] = ()
    raw_data: dict[str, Any] = field(default_factory=dict)
    alias_urls: tuple[str, ...] = ()
    # Stage 6E.1 provenance fields are optional so every historical packaged
    # ProductCard JSON remains deserializable without a migration/rewrite.
    price_type: str = "unknown"
    price_raw: str = ""
    price_source: str = ""

    def as_jsonable(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("price", "old_price", "quantity"):
            value[key] = None if value[key] is None else format(value[key], "f")
        return value

    @classmethod
    def from_jsonable(cls, value: dict[str, Any]) -> "ProductCard":
        data = dict(value)
        for key in ("price", "old_price", "quantity"):
            data[key] = None if data.get(key) in (None, "") else Decimal(str(data[key]))
        data["redirect_urls"] = tuple(data.get("redirect_urls", ()))
        data["alias_urls"] = tuple(data.get("alias_urls", ()))
        data["properties"] = tuple(PropertyValue(**item) for item in data.get("properties", ()))
        data["media"] = tuple(MediaItem(**item) for item in data.get("media", ()))
        data["categories"] = tuple(CategoryNode(**item) for item in data.get("categories", ()))
        data["options"] = tuple(ProductOption(
            name=item["name"], position=item.get("position", 0),
            values=tuple(OptionValue(**child) for child in item.get("values", ())),
        ) for item in data.get("options", ()))
        return cls(**data)


@dataclass(frozen=True)
class FetchRecord:
    requested_url: str
    final_url: str
    status_code: int
    redirects: tuple[str, ...]
    attempts: int
    elapsed_ms: int
    text: str


@dataclass(frozen=True)
class DiscoveryResult:
    sitemap_index_url: str
    sitemap_urls: tuple[str, ...]
    product_sitemap_urls: tuple[str, ...]
    product_urls: tuple[str, ...]
