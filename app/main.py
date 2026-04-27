
import logging

from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path as FilePath
from fastapi.responses import FileResponse

from app.Agents.db import close_pool, init_pool
from app.config import settings
from app.routers.endpoints import router as chat_router

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

DATABASE_URL = settings.DATABASE_URL
TEST_CHAT_UI = FilePath(__file__).resolve().parent / "static" / "test-chat.html"



@asynccontextmanager
async def lifespan(_: FastAPI):
    init_pool(settings.DATABASE_URL)
    try:
        yield
    finally:
        close_pool()


app = FastAPI(
    title="StayEase AI Agent API",
    version="1.0.0",
    description="Conversational booking agent for StayEase Bangladesh",
    lifespan=lifespan,
)

app.include_router(chat_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/")
def read_root():
    return {"message": "Welcome to StayEase Agent API"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/test-chat")
def test_chat_ui():
    return FileResponse(TEST_CHAT_UI)





if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
