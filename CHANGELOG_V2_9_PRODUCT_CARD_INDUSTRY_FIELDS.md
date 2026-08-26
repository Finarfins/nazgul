# V2.9 Product Card Industry Fields

- Product schema now includes OEM number, alternative OEM numbers, brand, manufacturer, compatible models, rack/location and technical notes.
- Product search includes OEM, alternative OEM, brand and compatible model fields.
- Product create/update APIs persist the new fields.
- Product dialog exposes the new Sungur Tarım-specific fields.
- Products list shows OEM and rack/location columns.
- Alembic revision: 20260715_0007.

## Validation
- backend/test_v2_9_product_industry_fields.py: 1 passed
- Python compileall: passed
- Frontend Vitest/build: not rerun because node_modules is intentionally absent from the checkpoint.
