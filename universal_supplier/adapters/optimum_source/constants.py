SUPPLIER_CODE = "optimum"
BASE_URL = "https://optimum.su/"
CATALOG_URL = f"{BASE_URL}catalog/"
SITEMAP_URL = f"{BASE_URL}sitemap.xml"
USER_AGENT = "UniversalSupplierFeed/1.0 (+read-only supplier catalog adapter)"
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "yclid", "gclid", "fbclid", "from"}
COLLECTION_PATHS = {"/catalog/novinki/": "new", "/catalog/rasprodazha/": "sale", "/shares/": "promotion"}
MERCHANDISING_PATHS = {
    "/catalog/rasprodazha/",
    "/catalog/aktsiya_na_stanki/",
    "/catalog/aktsiya_na_osnastku/",
    "/catalog/novinki/",
}
MERCHANDISING_NAMES = {"распродажа", "акция на станки", "акция на оснастку", "новинки"}
