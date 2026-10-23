from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Vite dev server; tighten to the real frontend origin when deploying.
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def add_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,  # the refresh-token cookie travels with the page's calls to /auth
        allow_methods=["*"],
        allow_headers=["*"],
    )
