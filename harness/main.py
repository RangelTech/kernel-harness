"""Scaffold do kernel-harness: apenas health/version. Funcionalidade real: spec 04."""
import os

from fastapi import FastAPI

from harness import __version__

app = FastAPI(title="kernel-harness", version=__version__)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "kernel-harness", "version": __version__}


@app.get("/version")
def version() -> dict:
    return {
        "version": __version__,
        "commit": os.environ.get("GIT_COMMIT", "unknown"),
        "hermes_pinned": os.environ.get("HERMES_IMAGE", "nousresearch/hermes-agent:v0.22.2"),
    }
