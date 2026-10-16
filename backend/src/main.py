import uvicorn

if __name__ == "__main__":
    # Local dev: `uv run python src/main.py` from backend/. Compose runs uvicorn directly.
    uvicorn.run("api.rest.app:app", host="127.0.0.1", port=8000, reload=False)
