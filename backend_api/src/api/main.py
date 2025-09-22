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

# Pillow for real image editing
from PIL import Image, ImageFilter, ImageOps

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


# PUBLIC_INTERFACE
class EditOperation(str, Enum):
    """Supported edit operations."""
    resize = "resize"
    crop = "crop"
    rotate = "rotate"
    flip = "flip"
    flop = "flop"
    grayscale = "grayscale"
    blur = "blur"
    sharpen = "sharpen"
    autocontrast = "autocontrast"


# PUBLIC_INTERFACE
class EditParams(BaseModel):
    """Parameters for specific edit operations.

    Depending on 'operation', relevant fields are:
    - resize: width, height, keep_aspect (optional)
    - crop: x, y, width, height
    - rotate: angle, expand (optional)
    - flip/flop: no params
    - grayscale: no params
    - blur: radius (float)
    - sharpen: factor (float)  (uses ImageFilter.UnsharpMask)
    - autocontrast: cutoff (float 0..100), ignore (optional)
    """
    width: Optional[int] = Field(None, description="Target width for resize or crop.")
    height: Optional[int] = Field(None, description="Target height for resize or crop.")
    keep_aspect: Optional[bool] = Field(True, description="Maintain aspect ratio for resize when one dimension is missing.")
    x: Optional[int] = Field(None, description="Left coordinate for crop.")
    y: Optional[int] = Field(None, description="Top coordinate for crop.")
    angle: Optional[float] = Field(None, description="Rotation angle in degrees.")
    expand: Optional[bool] = Field(True, description="Expand canvas to fit entire rotated image.")
    radius: Optional[float] = Field(2.0, description="Blur radius for Gaussian blur.")
    factor: Optional[float] = Field(1.5, description="Sharpen factor; used for unsharp mask.")
    cutoff: Optional[float] = Field(0.0, description="Autocontrast cutoff percentage 0..100.")
    ignore: Optional[List[int]] = Field(None, description="Values to ignore in autocontrast histogram, typically [0,255].")


# PUBLIC_INTERFACE
class EditRequest(BaseModel):
    """JSON request describing an edit to apply to a stored image."""
    operation: EditOperation = Field(..., description="Edit operation to apply.")
    params: Optional[EditParams] = Field(None, description="Parameters for the edit operation.")
    output_format: Optional[str] = Field(None, description="Optional output format override, e.g., 'PNG' or 'JPEG'.")


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


# Minimal "processing" utilities. In real-life, use Pillow/OpenCV. Here we'll simulate and add real edits.

def _simulate_processing(source_path: SysPath, dest_path: SysPath, operation: str, params: Dict[str, str]):
    """
    Simulate image processing by copying the original file to processed path.
    For certain operations, we append small headers to indicate processing metadata.
    """
    if not source_path.exists():
        raise FileNotFoundError("Source image not found for processing.")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with source_path.open("rb") as src, dest_path.open("wb") as dst:
        note = f"Processed-Op:{operation};Params:{params}\n".encode("utf-8")
        dst.write(note)
        shutil.copyfileobj(src, dst)


def _apply_edit_pillow(src_path: SysPath, dst_path: SysPath, edit: EditRequest) -> Dict[str, int]:
    """
    Apply an image edit using Pillow and save to destination.
    Returns resulting dimensions: {'width': int, 'height': int}
    """
    if not src_path.exists():
        raise FileNotFoundError("Source image not found for editing.")
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src_path) as img:
        op = edit.operation
        p = edit.params or EditParams()

        if op == EditOperation.resize:
            # Determine target size
            if p.width and p.height:
                size = (p.width, p.height)
            elif p.width and not p.height:
                if p.keep_aspect:
                    ratio = p.width / float(img.width)
                    size = (p.width, int(img.height * ratio))
                else:
                    size = (p.width, img.height)
            elif p.height and not p.width:
                if p.keep_aspect:
                    ratio = p.height / float(img.height)
                    size = (int(img.width * ratio), p.height)
                else:
                    size = (img.width, p.height)
            else:
                raise HTTPException(status_code=400, detail="Resize requires width or height.")
            img = img.resize(size)

        elif op == EditOperation.crop:
            if p.x is None or p.y is None or p.width is None or p.height is None:
                raise HTTPException(status_code=400, detail="Crop requires x, y, width, height.")
            left = int(max(0, p.x))
            top = int(max(0, p.y))
            right = int(min(img.width, left + p.width))
            bottom = int(min(img.height, top + p.height))
            if right <= left or bottom <= top:
                raise HTTPException(status_code=400, detail="Crop region is empty or invalid.")
            img = img.crop((left, top, right, bottom))

        elif op == EditOperation.rotate:
            if p.angle is None:
                raise HTTPException(status_code=400, detail="Rotate requires angle.")
            img = img.rotate(p.angle, expand=bool(p.expand))

        elif op == EditOperation.flip:
            img = ImageOps.flip(img)

        elif op == EditOperation.flop:
            img = ImageOps.mirror(img)

        elif op == EditOperation.grayscale:
            img = ImageOps.grayscale(img)

        elif op == EditOperation.blur:
            radius = float(p.radius or 2.0)
            img = img.filter(ImageFilter.GaussianBlur(radius=radius))

        elif op == EditOperation.sharpen:
            factor = float(p.factor or 1.5)
            # Using UnsharpMask for controllable sharpening
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(150 * factor), threshold=3))

        elif op == EditOperation.autocontrast:
            cutoff = float(p.cutoff or 0.0)
            ignore = p.ignore
            img = ImageOps.autocontrast(img, cutoff=cutoff, ignore=ignore)

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported edit operation: {op}")

        # Save output
        save_kwargs = {}
        if edit.output_format:
            fmt = edit.output_format.upper()
        else:
            fmt = img.format or "PNG"  # fallback if no format
        img.save(dst_path, format=fmt, **save_kwargs)
        return {"width": img.width, "height": img.height}


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
        {"name": "editing", "description": "Image editing operations like crop, rotate, resize, filters."},
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


def _edited_path(image_id: str, filename: str, suffix: str = "") -> SysPath:
    """
    Build a path for edited outputs. Suffix can be used to differentiate versions.
    """
    base = f"{image_id}__edited{suffix}__{filename}"
    return store.proc_dir / base


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
@app.post(
    "/api/images/{image_id}/edit",
    tags=["editing"],
    summary="Apply an edit operation to an image",
    response_model=ImageMeta,
    responses={
        200: {"description": "Edit applied and stored."},
        404: {"description": "Image not found."},
        400: {"description": "Invalid parameters or unsupported operation."},
    },
)
def edit_image(image_id: str, req: EditRequest):
    """
    Apply an image edit synchronously using Pillow and store the edited image in the processed directory.
    The image metadata will be updated with the new width/height and status 'completed'.
    """
    rec = _ensure_exists(image_id)
    src = _original_path(image_id, rec["filename"])
    if not src.exists():
        raise HTTPException(status_code=404, detail="Original content not found")

    # Build destination path (overwrite the "processed" location to be retrievable via /processed)
    dst = _processed_path(image_id, rec["filename"])
    try:
        dims = _apply_edit_pillow(src, dst, req)
        rec["status"] = ProcessingStatus.completed
        rec["width"] = dims.get("width")
        rec["height"] = dims.get("height")
        rec["error"] = None
    except HTTPException:
        # pass through
        raise
    except Exception as e:
        rec["status"] = ProcessingStatus.failed
        rec["error"] = str(e)
        rec["updated_at"] = datetime.utcnow()
        store.upsert(rec)
        raise HTTPException(status_code=400, detail=f"Edit failed: {e}")

    rec["updated_at"] = datetime.utcnow()
    store.upsert(rec)
    return store.to_model(rec)


# PUBLIC_INTERFACE
@app.get(
    "/api/images/{image_id}/edited",
    tags=["content"],
    summary="Get last edited image bytes",
    responses={
        200: {"description": "Edited image content."},
        404: {"description": "Edited content not available."},
    },
)
def get_edited(image_id: str):
    """
    Return the last edited image (same as processed for now) as bytes.
    """
    rec = _ensure_exists(image_id)
    dst = _processed_path(image_id, rec["filename"])
    if not dst.exists():
        raise HTTPException(status_code=404, detail="Edited content not available.")

    def iterfile():
        with dst.open("rb") as f:
            yield from f

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
