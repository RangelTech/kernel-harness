"""Backend FALSO (agent-platform) em memoria: implementa o Protocol `Backend` e, para o
compose de dev, expoe os mesmos endpoints internos em HTTP (`build_http_app`)."""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeBackend:
    def __init__(self) -> None:
        self.usage: list[dict] = []
        self.messages: list[dict] = []
        self.skill_proposals: list[dict] = []
        self.memory_proposals: list[dict] = []
        self.approvals: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.charges: dict[str, dict] = {}
        self.user_skills: dict[tuple[str, str], list[dict]] = {}
        self.docs: list[dict] = []
        self.memories: dict[tuple[str, str], list[dict]] = {}
        self.packages: dict[str, dict] = {}
        self.fail_post_message = False
        self.calls: list[tuple[str, dict]] = []

    # ---- Protocol
    async def post_usage(self, run_id, token, payload):
        self.calls.append(("usage", payload))
        self.usage.append({"run_id": run_id, "token": token, **payload})

    async def post_message(self, run_id, token, payload):
        if self.fail_post_message:
            raise RuntimeError("backend fora")
        self.messages.append({"run_id": run_id, **payload})

    async def post_skill_proposal(self, run_id, token, payload):
        sid = str(uuid.uuid4())
        self.skill_proposals.append({"id": sid, "run_id": run_id, **payload})
        return {"id": sid, "status": "pending_review"}

    async def post_memory_proposal(self, run_id, token, payload):
        self.memory_proposals.append({"run_id": run_id, **payload})
        return {"status": "pending_review"}

    async def get_user_skills(self, tenant_id, user_id):
        return list(self.user_skills.get((tenant_id, user_id), []))

    async def create_approval(self, run_id, token, payload):
        aid = "ap_" + uuid.uuid4().hex[:10]
        self.approvals[aid] = {"run_id": run_id, **payload}
        return aid

    async def artifact_register_init(self, run_id, token, payload):
        aid = str(uuid.uuid4())
        self.artifacts[aid] = {"init": payload}
        return {"artifact_id": aid, "upload_url": f"https://storage.fake/upload/{aid}"}

    async def artifact_register_complete(self, run_id, token, payload):
        self.artifacts.setdefault(payload["artifact_id"], {})["complete"] = payload
        return {"ok": True}

    async def record_charge(self, run_id, token, payload):
        self.charges[payload["reference_id"]] = {**self.charges.get(payload["reference_id"], {}),
                                                 **payload}

    async def lookup_charge(self, run_id, token, reference_id):
        return self.charges.get(reference_id)

    async def search_docs(self, run_id, token, payload):
        q = payload["query"].lower()
        return [d for d in self.docs if d.get("tenant_id") == payload["tenant_id"]
                and q in d["text"].lower()][: payload.get("top_k", 5)]

    async def recall_memories(self, run_id, token, payload):
        return list(self.memories.get((payload["tenant_id"], payload["user_id"]), []))

    async def fetch_package(self, url):
        return self.packages[url]

    async def aclose(self):
        return None


def build_http_app(state: FakeBackend | None = None) -> FastAPI:
    """Mesmos endpoints internos do `HttpBackend`, para o compose de dev."""
    st = state or FakeBackend()
    app = FastAPI(title="fake-backend")
    app.state.backend = st

    @app.post("/internal/runs/{run_id}/usage")
    async def usage(run_id: str, request: Request):
        await st.post_usage(run_id, "", await request.json())
        return {}

    @app.post("/internal/runs/{run_id}/messages")
    async def messages(run_id: str, request: Request):
        await st.post_message(run_id, "", await request.json())
        return {}

    @app.post("/internal/runs/{run_id}/skills")
    async def skills(run_id: str, request: Request):
        return await st.post_skill_proposal(run_id, "", await request.json())

    @app.post("/internal/runs/{run_id}/memory-proposals")
    async def memprop(run_id: str, request: Request):
        return await st.post_memory_proposal(run_id, "", await request.json())

    @app.get("/internal/skills/user/{tenant_id}/{user_id}")
    async def user_skills(tenant_id: str, user_id: str):
        return {"skills": await st.get_user_skills(tenant_id, user_id)}

    @app.post("/internal/approvals")
    async def approvals(request: Request):
        return {"approval_id": await st.create_approval("", "", await request.json())}

    @app.post("/internal/runs/{run_id}/artifacts/register-init")
    async def reg_init(run_id: str, request: Request):
        return await st.artifact_register_init(run_id, "", await request.json())

    @app.post("/internal/runs/{run_id}/artifacts/register-complete")
    async def reg_complete(run_id: str, request: Request):
        return await st.artifact_register_complete(run_id, "", await request.json())

    @app.post("/internal/runs/{run_id}/payments")
    async def pay(run_id: str, request: Request):
        await st.record_charge(run_id, "", await request.json())
        return {}

    @app.get("/internal/payments/charges")
    async def charges(reference_id: str):
        c = await st.lookup_charge("", "", reference_id)
        return JSONResponse(c, status_code=200) if c else JSONResponse({}, status_code=404)

    @app.post("/internal/search/docs")
    async def docs(request: Request):
        return {"results": await st.search_docs("", "", await request.json())}

    @app.post("/internal/memories/recall")
    async def recall(request: Request):
        return {"memories": await st.recall_memories("", "", await request.json())}

    @app.put("/upload/{aid}")
    async def upload(aid: str):
        return {}

    return app
