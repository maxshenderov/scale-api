from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import warehouse, connections, snapshots
from config import settings

app = FastAPI(title=settings.app_title, version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(warehouse.router)
app.include_router(connections.router)
app.include_router(snapshots.router)


@app.get("/api/ping")
async def ping():
    return {"ok": True, "version": settings.app_version}
