import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from server import chat_response


ROOT = Path(__file__).resolve().parent
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def home():
    return HTMLResponse((ROOT / "index.html").read_text(encoding="utf-8"))


@app.post("/api/chat")
async def chat(request: Request):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "The request was not valid JSON."},
            )
        if length <= 0 or length > 64 * 1024:
            return JSONResponse(
                status_code=400,
                content={"error": "Send a message under 64 KB."},
            )

    body = await request.body()
    status, payload = await asyncio.to_thread(chat_response, body)
    return JSONResponse(status_code=status, content=payload)
