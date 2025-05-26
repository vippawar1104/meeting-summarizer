# src/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import settings, logger
from api.endpoints import transcription
from api.endpoints import llm as llm_router

app = FastAPI(
    title="Multilingual Note-Taking Agent API",
    description="API for the Multilingual Note-Taking Agent",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json" 
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows all origins for easy Streamlit integration locally
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods
    allow_headers=["*"], # Allows all headers
)
logger.info("CORS middleware added with permissive settings for development.")

# Include API routers
app.include_router(transcription.router, prefix=settings.API_V1_STR)
app.include_router(llm_router.router, prefix=f"{settings.API_V1_STR}/llm")

@app.get("/ping", tags=["Health"])
async def ping():
    """Basic health check endpoint."""
    logger.info("Ping endpoint called")
    return {"message": "pong"}


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server directly (for debugging)...")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)

