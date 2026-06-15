"""
Demo-site static server with in-memory drift injection.

Routes:
  GET  /{path}         — serve static file (patched version if active)
  POST /_qa/inject     — apply a find/replace patch to one file
  POST /_qa/reset      — clear all patches
"""
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(docs_url=None, redoc_url=None)

# filename → patched content (cleared by /_qa/reset)
_patches: dict[str, str] = {}


class InjectRequest(BaseModel):
    file: str    # e.g. "checkout.html"
    find: str
    replace: str


@app.post("/_qa/inject")
def inject(req: InjectRequest):
    filepath = os.path.join(STATIC_DIR, req.file)
    if not os.path.isfile(filepath):
        raise HTTPException(404, f"{req.file} not found in static dir")
    # start from current patch if already patched, else from disk
    if req.file in _patches:
        base = _patches[req.file]
    else:
        with open(filepath, encoding="utf-8") as f:
            base = f.read()
    if req.find not in base:
        raise HTTPException(400, f"find string not present in {req.file}")
    _patches[req.file] = base.replace(req.find, req.replace, 1)
    return {"status": "injected", "file": req.file}


@app.post("/_qa/reset")
def reset():
    cleared = list(_patches.keys())
    _patches.clear()
    return {"status": "reset", "cleared": cleared}


@app.get("/{path:path}")
def serve(path: str = ""):
    if not path or path in ("", "/"):
        path = "index.html"
    if path in _patches:
        media = "text/css; charset=utf-8" if path.endswith(".css") else "text/html; charset=utf-8"
        return Response(_patches[path], media_type=media)
    filepath = os.path.join(STATIC_DIR, path)
    if not os.path.isfile(filepath):
        raise HTTPException(404, path)
    return FileResponse(filepath)
