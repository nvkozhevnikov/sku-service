"""Read-only presentation union; it never joins IDs across PostgreSQL databases."""
from __future__ import annotations
from .store import ProductFilters

class CombinedReadOnlyStore:
    read_only = True
    def __init__(self, *stores): self.stores = stores
    def health(self): return {"application":"ok", "postgresql":"multiple_read_only_sources", "sources":[s.health() for s in self.stores]}
    def dashboard(self):
        rows = {}
        for store in self.stores:
            for row in store.dashboard():
                current = rows.get(row.get("code"))
                if current is None or (current.get("products") is None and row.get("products") is not None):
                    rows[row.get("code")] = row
        return sorted(rows.values(), key=lambda row: str(row.get("name", "")))
    def suppliers(self): return self.dashboard()
    def filter_options(self):
        options=[s.filter_options() for s in self.stores]
        suppliers = {}
        for option in options:
            for supplier in option.get("suppliers", []):
                suppliers.setdefault(supplier["code"], supplier)
        return {"suppliers":list(suppliers.values()), "brands":sorted({x for o in options for x in o.get("brands",[])}), "decisions":(), "kinds":[]}
    def products(self, filters):
        if filters.supplier:
            owner = self._store(filters.supplier)
            if owner is not None:
                return owner.products(filters)
        rows=[]
        for store in self.stores:
            page = 1
            while True:
                result=store.products(ProductFilters(**{**filters.__dict__, "page":page, "page_size":100}))
                rows.extend(result["items"])
                if not result["items"] or page >= result["pages"]: break
                page += 1
        rows.sort(key=lambda row:(row.get("supplier_code",""),str(row.get("external_id",""))))
        total=len(rows); start=(filters.page-1)*filters.page_size
        return {"items":rows[start:start+filters.page_size],"total":total,"page":filters.page,"page_size":filters.page_size,"pages":max(1,(total+filters.page_size-1)//filters.page_size)}
    def _store(self, code):
        """Choose the source that actually owns the product, not its unavailable placeholder."""
        fallback = None
        for store in self.stores:
            row = next((item for item in store.suppliers() if item.get("code") == code), None)
            if row is None:
                continue
            fallback = fallback or store
            if row.get("products") is not None:
                return store
        return fallback
    def product(self, code, external_id):
        store=self._store(code); return store.product(code,external_id) if store else None
    def product_history(self, code, external_id):
        store=self._store(code); return store.product_history(code,external_id) if store else []
    def runs(self, limit=100): return [row for s in self.stores for row in s.runs(limit)]
    def run(self, run_id): return None
