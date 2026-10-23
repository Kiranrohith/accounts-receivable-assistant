from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import get_settings


def add_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,  # CORS_ALLOWED_ORIGINS: the real frontend address when deployed
        allow_credentials=True,  # the refresh-token cookie travels with the page's calls to /auth
        allow_methods=["*"],
        allow_headers=["*"],
    )
