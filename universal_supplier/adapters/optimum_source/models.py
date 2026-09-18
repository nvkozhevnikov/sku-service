from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(StrictModel):
    external_id: str | None = None
    external_id_type: Literal["internal_product_id", "canonical_url"] | None = None
    internal_product_id: str | None = None
    sku: str | None = None
    name: str | None = None
    brand_raw: str | None = None
    manufacturer_raw: str | None = None
    source_url: str | None = None
    canonical_url: str | None = None


class Commercial(StrictModel):
    price: str | None = None
    old_price: str | None = None
    currency: str = "RUB"
    availability_raw: str | None = None
    availability_normalized: str | None = None
    quantity: int | None = None


class Description(StrictModel):
    text: str | None = None
    html: str | None = None


class Property(StrictModel):
    raw_name: str
    raw_value: str
    raw_unit: str | None = None
    raw_value_html: str | None = None
    position: int
    is_empty_like: bool = False


class Category(StrictModel):
    name: str
    url: str | None = None
    position: int


class Image(StrictModel):
    source_url: str
    position: int
    is_primary: bool = False
    alt: str | None = None
    title: str | None = None


class Document(StrictModel):
    source_url: str
    title: str | None = None
    document_type: str | None = None
    position: int


class Video(StrictModel):
    source_url: str
    provider: str | None = None
    title: str | None = None
    position: int


class PackageItem(StrictModel):
    text: str
    position: int


class Relation(StrictModel):
    relation_type: str
    target_name: str | None = None
    target_url: str | None = None
    target_sku: str | None = None
    price: str | None = None
    availability: str | None = None
    position: int


class CanonicalSourceProduct(StrictModel):
    supplier: str = "optimum"
    identity: Identity
    commercial: Commercial = Field(default_factory=Commercial)
    description: Description = Field(default_factory=Description)
    categories: list[Category] = Field(default_factory=list)
    properties: list[Property] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    videos: list[Video] = Field(default_factory=list)
    package_contents: list[PackageItem] = Field(default_factory=list)
    related_products: list[Relation] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    raw_data: dict = Field(default_factory=dict)
    parse_status: str = "OK"

    @model_validator(mode="after")
    def validate_minimum(self):
        i = self.identity
        if not i.name or not i.source_url or not i.canonical_url:
            raise ValueError("name, source_url and canonical_url are required")
        if not i.external_id or not i.external_id_type:
            self.parse_status = "REVIEW_IDENTITY"
        return self


class DiscoveryRecord(StrictModel):
    source_url: str
    canonical_url: str
    discovery_source: str
    discovery_category: str | None = None
    discovered_at: str
    collections: list[str] = Field(default_factory=list)
