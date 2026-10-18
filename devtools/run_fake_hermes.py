"""Executavel do Hermes falso para o compose de dev: python -m devtools.run_fake_hermes"""
import os

import uvicorn

from devtools.fake_hermes import FakeHermes

fake = FakeHermes(
    os.environ.get("PROFILES_ROOT", "/srv/hermes/profiles"),
    broker_url=os.environ.get("RT_BROKER_URL", ""),
    broker_token=os.environ.get("RT_BROKER_TOKEN", ""),
    key=os.environ.get("HERMES_API_KEY", ""),
    max_concurrent=int(os.environ.get("HERMES_MAX_CONCURRENT_RUNS", "4")),
)
app = fake.app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8642")), log_level="info")
