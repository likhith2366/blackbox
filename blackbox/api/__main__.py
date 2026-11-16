"""python -m blackbox.api  ->  http://127.0.0.1:8700 (bound to localhost only)."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("blackbox.api.server:app", host="127.0.0.1", port=8700, log_level="info")
