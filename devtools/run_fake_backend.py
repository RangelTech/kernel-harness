"""Executavel do backend falso (endpoints /internal/*): python -m devtools.run_fake_backend"""
import os

import uvicorn

from devtools.fake_backend import build_http_app

app = build_http_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), log_level="info")
