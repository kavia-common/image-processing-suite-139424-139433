import os
from datetime import datetime
from typing import List, Optional, Dict

from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Response, status, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from enum import Enum
import uuid
import shutil
from pathlib import Path as SysPath

# PUBLIC_INTERFACE
class ProcessingStatus(str, Enum):
    """Status values for an image processing job."""
    uploaded = "uploaded"
    processing = "processing"
    completed = "completed"
    failed = "failed"


# PUBLIC_INTERFACE
class ImageMeta(BaseModel):
    """Metadata describing a stored image."""
    id: str = Field(..., description="Unique image identifier (UUID).")
    filename: str = Field(..., description="Original filename uploaded by the user.")
    content_type: str = Field(..., description="MIME type of the uploaded file.")
    size_bytes: int = Field(..., description="Size of the original file in bytes.")
    status: ProcessingStatus = Field(..., description="Processing status of the image.")
    created_at: datetime = Field(..., description="Timestamp when the image was created.")
    updated_at: datetime = Field(..., description="Timestamp when the image was last updated.")
    width: Optional[int] = Field(None, description="Width (px) if known after processing.")
    height: Optional[int] = Field(None, description="Height (px) if known after processing.")
    error: Optional[str] = Field(None, description="Error message if processing failed.")


# PUBLIC_INTERFACE
class ImageListResponse(BaseModel):
    """Paginated list response of images."""
    items: List[ImageMeta] = Field(..., description="List of image metadata.")
    total: int = Field(..., description="Total images available.")
    page: int = Field(..., description="Current page number (1-based).")
    page_size: int = Field(..., description="Number of items per page.")


# PUBLIC_INTERFACE
class ProcessRequest(BaseModel):
    """Request body for triggering/adjusting processing."""
    operation: str = Field(..., description="Processing operation (e.g., 'grayscale', 'thumbnail', 'rotate').")
    params: Optional[Dict[str, str]] = Field(default_factory=dict, description="Parameters for the operation (e.g., angle=90).")


# Simple in-memory store mocking a database layer. Replace with an actual DB later.
class _ImageStore:
    def __init__(self, base_dir: str):
        self._items: Dict[str, Dict] = {}
        self.base_dir = SysPath(base_dir)
        self.orig_dir = self.base_dir / "originals"
        self.proc_dir = self.base_dir / "processed"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.orig_dir.mkdir(parents=True, exist_ok=True)
        self.proc_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> List[Dict]:
        return list(self._items.values())

    def get(self, image_id: str) -> Optional[Dict]:
        return self._items.get(image_id)

    def upsert(self, record: Dict):
        self._items[record["id"]] = record

    def delete(self, image_id: str) -> Optional[Dict]:
        return self._items.pop(image_id, None)

    def to_model(self, record: Dict) -> ImageMeta:
        return ImageMeta(**record)


# Minimal "processing" utilities. In real-life, use Pillow/OpenCV. Here we'll simulate.
def _simulate_processing(source_path: SysPath, dest_path: SysPath, operation: str, params: Dict[str, str]):
    """
    Simulate image processing by copying the original file to processed path.
    For certain operations, we append small headers to indicate processing metadata.
    """
    # For safety, ensure the source exists
    if not source_path.exists():
        raise FileNotFoundError("Source image not found for processing.")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate a processing change: copy file bytes; optionally prepend a small note
    with source_path.open("rb") as src, dest_path.open("wb") as dst:
        note = f"Processed-Op:{operation};Params:{params}\n".encode("utf-8")
        # This is NOT a valid image transformation; only a placeholder to simulate processing.
        # In a real system, replace with actual image transformation code.
        dst.write(note)
        shutil.copyfileobj(src, dst)


def _get_env(name: str, default: str) -> str:
    val = os.getenv(name, default)
    return val or default


DATA_DIR = _get_env("DATA_DIR", "/tmp/image_processing_data")

app = FastAPI(
    title="Image Processing Suite - Backend API",
    description=(
        "REST API for uploading images, triggering/simulating processing, tracking status, "
        "and retrieving original or processed outputs. "
        "Note: This demo uses an in-memory store and simulated processing. "
        "Replace with a database and real processing pipeline for production."
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "health", "description": "Health and diagnostics."},
        {"name": "images", "description": "Image upload, metadata, listing, and deletion."},
        {"name": "processing", "description": "Trigger and check image processing status."},
        {"name": "content", "description": "Retrieve original and processed image content."},
        {"name": "websocket", "description": "Real-time updates (reserved for future use)."},
    ],
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = _ImageStore(DATA_DIR)


# Helpers
def _ensure_exists(image_id: str) -> Dict:
    rec = store.get(image_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Image not found")
    return rec


def _original_path(image_id: str, filename: str) -> SysPath:
    return store.orig_dir / f"{image_id}__{filename}"


def _processed_path(image_id: str, filename: str) -> SysPath:
    return store.proc_dir / f"{image_id}__{filename}"


# Routes

# PUBLIC_INTERFACE
@app.get("/", tags=["health"], summary="Health Check")
def health_check():
    """Simple health check endpoint."""
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.post(
    "/api/images",
    status_code=status.HTTP_201_CREATED,
    tags=["images"],
    summary="Upload an image",
    response_model=ImageMeta,
    responses={
        201: {"description": "Image uploaded successfully."},
        400: {"description": "Invalid file or content type."},
    },
)
async def upload_image(file: UploadFile = File(..., description="Image file to upload.")):
    """
    Upload an image file. Stores metadata and the original file, with initial status 'uploaded'.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    image_id = str(uuid.uuid4())
    created_at = datetime.utcnow()
    meta = {
        "id": image_id,
        "filename": file.filename,
        "content_type": file.content_type or "application/octet-stream",
        "size_bytes": 0,
        "status": ProcessingStatus.uploaded,
        "created_at": created_at,
        "updated_at": created_at,
        "width": None,
        "height": None,
        "error": None,
    }

    dest = _original_path(image_id, file.filename)

    # Persist file
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)

    meta["size_bytes"] = size
    meta["updated_at"] = datetime.utcnow()
    store.upsert(meta)

    return store.to_model(meta)


# PUBLIC_INTERFACE
@app.get(
    "/api/images",
    tags=["images"],
    summary="List images (paginated)",
    response_model=ImageListResponse,
)
def list_images(
    page: int = Query(1, ge=1, description="Page number (1-based)."),
    page_size: int = Query(20, ge=1, le=200, description="Items per page."),
):
    """
    Returns a paginated list of images with metadata.
    """
    items = [store.to_model(x) for x in store.list()]
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return ImageListResponse(items=items[start:end], total=total, page=page, page_size=page_size)


# PUBLIC_INTERFACE
@app.get(
    "/api/images/{image_id}",
    tags=["images"],
    summary="Get image metadata",
    response_model=ImageMeta,
    responses={404: {"description": "Image not found."}},
)
def get_image_metadata(image_id: str = Path(..., description="Image ID (UUID).")):
    """
    Return metadata for a given image.
    """
    rec = _ensure_exists(image_id)
    return store.to_model(rec)


# Background task to simulate processing
def _process_background(image_id: str, operation: str, params: Dict[str, str]):
    rec = store.get(image_id)
    if not rec:
        return
    rec["status"] = ProcessingStatus.processing
    rec["updated_at"] = datetime.utcnow()
    store.upsert(rec)

    src = _original_path(image_id, rec["filename"])
    dst = _processed_path(image_id, rec["filename"])

    try:
        _simulate_processing(src, dst, operation, params or {})
        # We do not compute real dimensions; set placeholders
        rec["status"] = ProcessingStatus.completed
        rec["width"] = rec.get("width") or None
        rec["height"] = rec.get("height") or None
        rec["error"] = None
    except Exception as e:
        rec["status"] = ProcessingStatus.failed
        rec["error"] = str(e)
    finally:
        rec["updated_at"] = datetime.utcnow()
        store.upsert(rec)


# PUBLIC_INTERFACE
@app.post(
    "/api/images/{image_id}/process",
    tags=["processing"],
    summary="Trigger image processing",
    response_model=ImageMeta,
    responses={
        202: {"description": "Processing started."},
        404: {"description": "Image not found."},
        409: {"description": "Image is already processing."},
    },
    status_code=status.HTTP_202_ACCEPTED,
)
def process_image(
    image_id: str,
    req: ProcessRequest,
    bg: BackgroundTasks,
):
    """
    Start processing for a given image. This schedules a background task and returns current metadata.
    """
    rec = _ensure_exists(image_id)
    if rec["status"] == ProcessingStatus.processing:
        raise HTTPException(status_code=409, detail="Image is already processing")

    # Start background task
    bg.add_task(_process_background, image_id=image_id, operation=req.operation, params=req.params or {})
    rec["status"] = ProcessingStatus.processing
    rec["updated_at"] = datetime.utcnow()
    store.upsert(rec)
    return store.to_model(rec)


# PUBLIC_INTERFACE
@app.get(
    "/api/images/{image_id}/status",
    tags=["processing"],
    summary="Get processing status",
    response_model=ImageMeta,
    responses={404: {"description": "Image not found."}},
)
def get_status(image_id: str):
    """
    Get the processing status and latest metadata for an image.
    """
    rec = _ensure_exists(image_id)
    return store.to_model(rec)


# PUBLIC_INTERFACE
@app.get(
    "/api/images/{image_id}/original",
    tags=["content"],
    summary="Get original image bytes",
    responses={
        200: {"description": "Original image content."},
        404: {"description": "Image or content not found."},
    },
)
def get_original(image_id: str):
    """
    Return the original image file as bytes.
    """
    rec = _ensure_exists(image_id)
    src = _original_path(image_id, rec["filename"])
    if not src.exists():
        raise HTTPException(status_code=404, detail="Original content not found")

    def iterfile():
        with src.open("rb") as f:
            yield from f

    return StreamingResponse(iterfile(), media_type=rec["content_type"])


# PUBLIC_INTERFACE
@app.get(
    "/api/images/{image_id}/processed",
    tags=["content"],
    summary="Get processed image bytes",
    responses={
        200: {"description": "Processed image content."},
        404: {"description": "Processed content not available."},
    },
)
def get_processed(image_id: str):
    """
    Return the processed image file as bytes if processing completed.
    """
    rec = _ensure_exists(image_id)
    if rec["status"] != ProcessingStatus.completed:
        raise HTTPException(status_code=404, detail="Processed content not available yet")
    dst = _processed_path(image_id, rec["filename"])
    if not dst.exists():
        raise HTTPException(status_code=404, detail="Processed content missing on disk")

    def iterfile():
        with dst.open("rb") as f:
            yield from f

    # Media type remains original content_type for simplicity
    return StreamingResponse(iterfile(), media_type=rec["content_type"])


# PUBLIC_INTERFACE
@app.delete(
    "/api/images/{image_id}",
    tags=["images"],
    summary="Delete image and its content",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Image deleted."},
        404: {"description": "Image not found."},
    },
)
def delete_image(image_id: str):
    """
    Delete an image record and its on-disk files.
    """
    rec = _ensure_exists(image_id)
    src = _original_path(image_id, rec["filename"])
    dst = _processed_path(image_id, rec["filename"])

    # Remove metadata
    store.delete(image_id)

    # Best-effort file cleanup
    try:
        if src.exists():
            src.unlink()
        if dst.exists():
            dst.unlink()
    except Exception:
        # Avoid masking deletion with fs errors
        pass

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# PUBLIC_INTERFACE
@app.get(
    "/api/docs/websocket-usage",
    tags=["websocket"],
    summary="WebSocket usage help",
)
def websocket_usage():
    """
    WebSocket usage notes (reserved for future real-time updates):
    - Connect to ws://<host>/ws/images for real-time processing events.
    - Messages will include {image_id, status, progress}.
    - For this demo implementation, the WebSocket endpoint is not active yet.
    """
    return {
        "message": "WebSocket endpoint reserved for future use.",
        "notes": [
            "Connect to ws://<host>/ws/images for real-time updates (not implemented in this demo).",
            "Events would include image_id, status, and progress.",
        ],
    }
