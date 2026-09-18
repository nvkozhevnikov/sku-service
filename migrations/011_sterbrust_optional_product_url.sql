BEGIN;

-- The read-only Sterbrust registry contains legacy/inactive products for which
-- the API exposes no public URL. Keep those identity records without inventing
-- a URL; when a URL is present it must still be absolute HTTP(S).
ALTER TABLE sterbrust_products
    DROP CONSTRAINT sterbrust_products_url_ck,
    ALTER COLUMN product_url DROP NOT NULL,
    ADD CONSTRAINT sterbrust_products_url_ck
        CHECK (product_url IS NULL OR product_url ~ '^https?://');

COMMIT;
