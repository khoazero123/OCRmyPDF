import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import StreamingResponse
import tempfile
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal
import os
import requests

# Get max file size in MB from environment variable
max_file_size_in_mb = int(os.getenv("MAX_FILE_SIZE", 100))
disable_docs = os.getenv("API_DOCS_DISABLE", "false") == "true"
docs_url = os.getenv("API_DOCS_URL", "/docs") # /api/docs
redoc_url = os.getenv("API_REDOC_URL", "/redoc") # /api/redoc
openapi_url = os.getenv("API_OPENAPI_URL", "/openapi.json") # /openapi.json

# Get servers from environment variable
servers = os.getenv("API_SERVERS", "http://localhost:8000").split(",")
servers = [{"url": server, "description": server} for server in servers]

print(servers)

app = FastAPI(
    servers=servers,
    openapi_url=openapi_url, 
    docs_url=None if disable_docs else docs_url, 
    redoc_url=None if disable_docs else redoc_url,
)

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/ocr")
async def ocr_pdf(
    mode: Literal["skip-text", "normal", "force-ocr", "redo-ocr"] = "skip-text", 
    invalidate_digital_signatures: bool = False, 
    language: Literal["vie", "eng", "chi_sim", "chi_tra", "vie+eng", "vie+chi_sim+chi_tra", "vie+eng+chi_sim+chi_tra", "deu", "fra", "spa"] = "vie", 
    image_dpi: Annotated[int, Query(ge=1, le=5000)] = 300, 
    skip_big: bool = False, 
    oversample: Annotated[int, Query(ge=0, le=5000)] = 0, 
    rotate_pages: bool = False, 
    deskew: bool = False, 
    clean: bool = False, 
    clean_final: bool = False, 
    remove_vectors: bool = False, 
    output_type: Literal["pdfa", "pdf", "pdfa-1", "pdfa-2", "pdfa-3", "none"] = "pdfa", 
    pdf_renderer: Literal["auto", "hocr", "hocrdebug", "sandwich"] = "auto", 
    optimize: Literal["0", "1", "2", "3"] = "0", 
    title: str = None, 
    author: str = None, 
    keywords: str = None, 
    subject: str = None, 
    pages: str = None, 
    max_image_mpixels: Annotated[float, Query(ge=0.0), "Maximum image size in megapixels"] = None, 
    rotate_pages_threshold: Annotated[float, Query(ge=0.0, le=1000.0), "Threshold for automatic page rotation"] = 0.0, 
    fast_web_view: Annotated[float, Query(ge=0.0), "Linearize files above this size in MB"] = 1.0, 
    continue_on_soft_render_error: bool = True, 
    verbose: int = 1, 
    jpeg_quality: Annotated[int, Query(ge=0, le=100), "JPEG quality"] = 75, 
    png_quality: Annotated[int, Query(ge=0, le=100), "PNG quality"] = 75, 
    jbig2_lossy: bool = False, 
    jbig2_threshold: Annotated[int, Query(ge=0, le=100), "JBIG2 threshold"] = 0, 
    jobs: Annotated[int, Query(ge=1, le=os.cpu_count()), "Threads"] = os.cpu_count(),
    file: UploadFile|None|str = None,
    file_url: str = None,
):
    if not file and not file_url:
        raise HTTPException(status_code=400, detail="File or file_url is required")
    if file and file_url:
        raise HTTPException(status_code=400, detail="Only one of file or file_url is allowed")
    
    if file:
        if file and not file.filename.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp")):
            raise HTTPException(status_code=400, detail="File must be a PDF or image")
        # check file size > 0
        file_size = file.size
        if file.size == 0:
            print(f"File {file.filename} is empty")
            raise HTTPException(status_code=400, detail="File is empty")
        
        file_name = file.filename
        print(f"Processing file: {file.filename} with size {file.size} bytes")

        # Save uploaded file to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as input_tmp:
            input_tmp.write(await file.read())
            input_tmp.flush()
            input_path = input_tmp.name

    elif file_url:
        if not file_url.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp")):
            raise HTTPException(status_code=400, detail="File url must be a PDF or image")
        # get basename of file_url
        file_name = os.path.basename(file_url)
        # download file from url
        print(f"Downloading file from url: {file_url}")
        response = requests.get(file_url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to download file from url")
        file_content = response.content
        file_mime_type = response.headers.get("Content-Type")
        if not file_mime_type.startswith("application/pdf") and not file_mime_type.startswith("image/"):
            print(f"File {file_url} is not a PDF or image")
            raise HTTPException(status_code=400, detail="File is not a PDF or image")
        # save file to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as input_tmp:
            input_tmp.write(file_content)
            input_tmp.flush()
            input_path = input_tmp.name
        # get file size
        file_size = os.path.getsize(input_path)
        print(f"File {file_name} downloaded and saved to {input_path} with size {file_size} bytes")

    # check file size < max_file_size
    if file_size > (max_file_size_in_mb * 1024 * 1024):
        print(f"File {file_name} is too large, max size is {max_file_size_in_mb} MB")
        raise HTTPException(status_code=400, detail=f"File size must be less than {max_file_size_in_mb} MB")

    # Prepare output file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as output_tmp:
        output_path = output_tmp.name

    args = []
    if mode and mode != 'normal':
        args.append(f"--{mode}")
    if invalidate_digital_signatures:
        args.append("--invalidate-digital-signatures")
    if language:
        args.append(f"--language={language}")
    if not file_name.lower().endswith(".pdf") and image_dpi:
        args.append(f"--image-dpi={image_dpi}")
    if skip_big:
        args.append("--skip-big")
    if oversample:
        args.append(f"--oversample={oversample}")
    if rotate_pages:
        args.append("--rotate-pages")
    if deskew:
        args.append("--deskew")
    if clean:
        args.append("--clean")
    if clean_final:
        args.append("--clean-final")
    if remove_vectors:
        args.append("--remove-vectors")
    if output_type:
        args.append(f"--output-type={output_type}")
    if pdf_renderer:
        args.append(f"--pdf-renderer={pdf_renderer}")
    if optimize:
        args.append(f"--optimize={optimize}")
    if title:
        args.append(f"--title={title}")
    if author:
        args.append(f"--author={author}")
    if keywords:
        args.append(f"--keywords={keywords}")
    if subject:
        args.append(f"--subject={subject}")
    if pages:
        args.append(f"--pages={pages}")
    if max_image_mpixels:
        args.append(f"--max-image-mpixels={max_image_mpixels}")
    if rotate_pages_threshold:
        args.append(f"--rotate-pages-threshold={rotate_pages_threshold}")
    if fast_web_view:
        args.append(f"--fast-web-view={fast_web_view}")
    if continue_on_soft_render_error:
        args.append("--continue-on-soft-render-error")
    if verbose:
        args.append(f"--verbose={verbose}")
    if optimize > '0' and jpeg_quality:
        args.append(f"--jpeg-quality={jpeg_quality}")
    if optimize > '0' and png_quality:
        args.append(f"--png-quality={png_quality}")
    if jbig2_lossy:
        args.append("--jbig2-lossy")
    if jbig2_threshold:
        args.append(f"--jbig2-threshold={jbig2_threshold}")
    if jobs:
        args.append(f"--jobs={jobs}")

    args.append(input_path)
    args.append(output_path)

    # Build OCRmyPDF command
    args = [sys.executable, '-u', '-m', "ocrmypdf"] + args

    # Run OCRmyPDF
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not Path(output_path).exists():
        print(f"OCR failed: {proc.stderr.decode()}")
        raise HTTPException(status_code=500, detail=f"OCR failed: {proc.stderr.decode()}")

    # Stream the output PDF back to the client
    def iterfile():
        with open(output_path, "rb") as f:
            yield from f

    return StreamingResponse(iterfile(), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=ocr_{file_name}"
    })

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")

# curl -X POST "http://localhost:8000/ocr?invalidate_digital_signatures=true" -F "file=@input.pdf" --output ocr_output.pdf
