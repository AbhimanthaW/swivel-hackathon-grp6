"""Entry point: `python run.py` serves the API on http://127.0.0.1:8001."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8001, reload=False)
