# Image Processing Suite - Backend API

FastAPI backend for uploading, processing (simulated), listing, retrieving, and deleting images.

## Run

Install dependencies (Python 3.10+):

```bash
pip install -r requirements.txt
```

Start server:

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Optional environment variables:

- DATA_DIR: Directory to store originals/processed files (default: /tmp/image_processing_data)

## API

- GET /           Health check
- POST /api/images (multipart/form-data file=...) -> 201 Created, ImageMeta
- GET  /api/images -> ImageListResponse (paginated with ?page=&page_size=)
- GET  /api/images/{image_id} -> ImageMeta
- POST /api/images/{image_id}/process (JSON: {operation, params?}) -> 202 Accepted, ImageMeta
- GET  /api/images/{image_id}/status -> ImageMeta
- GET  /api/images/{image_id}/original -> bytes
- GET  /api/images/{image_id}/processed -> bytes (after completed)
- DELETE /api/images/{image_id} -> 204 No Content
- GET /api/docs/websocket-usage -> placeholder help

## OpenAPI
Regenerate the OpenAPI description after changes:

```bash
python -m src.api.generate_openapi
```

The file is written to `interfaces/openapi.json`.

## Notes
- This implementation uses an in-memory store and simulated processing. Replace `_ImageStore` with a database-backed repository and `_simulate_processing` with real image transformations (e.g., Pillow/OpenCV) for production.
