from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Vite dev server; tighten to the real frontend origin when deploying.
_ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def add_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
