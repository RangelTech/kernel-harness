"""Entrada do kernel-harness (uvicorn harness.main:app). Os componentes sobem no lifespan."""
from harness.api import create_app

app = create_app()
