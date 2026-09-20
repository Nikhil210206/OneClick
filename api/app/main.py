"""OneClick API entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.obs import readiness
from app.routes import device, metrics, stream, troubleshoot


@asynccontextmanager
async def lifespan(app: FastAPI):
    readiness.warm()  # catalog, Screen Graph, vector index, cache snapshot
    yield


app = FastAPI(title="OneClick", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health(response: Response) -> dict:
    """{"status": "ok"} only once the request path can actually serve; 503 until then (ADR-007)."""
    if not readiness.ensure():
        response.status_code = 503
        return {"status": "starting", **readiness.state()}
    return {"status": "ok"}


app.include_router(troubleshoot.router)
app.include_router(stream.router)
app.include_router(device.router)
app.include_router(metrics.router)
