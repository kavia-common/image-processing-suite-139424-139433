# image-processing-suite-139424-139433

This workspace contains the backend FastAPI service for an image processing suite.

- Backend: `backend_api/` (FastAPI)
- Interfaces: OpenAPI spec is generated to `backend_api/interfaces/openapi.json`

Run backend locally:

```bash
cd backend_api
pip install -r requirements.txt
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```