# src/core/config.py
import os
from pydantic_settings import BaseSettings
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings(BaseSettings):
    """Application settings."""
    APP_NAME: str = "Multilingual Note-Taking Agent API"
    API_V1_STR: str = "/api/v1"
    ASSEMBLYAI_API_KEY: str = os.getenv("ASSEMBLYAI_API_KEY")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY")
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"

    # LangChain specific settings (optional, for text splitting)
    CHUNK_SIZE: int = 4000
    CHUNK_OVERLAP: int = 200

    # Temporary directory for uploads
    UPLOAD_DIR: str = "temp_audio"

    class Config:
        case_sensitive = True
        env_file = '.env'
        env_file_encoding = 'utf-8'

settings = Settings()

# Ensure the upload directory exists
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

# Basic logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Settings loaded.")
if not settings.ASSEMBLYAI_API_KEY:
    logger.warning("ASSEMBLYAI_API_KEY is not set in the environment variables or .env file!")
if not settings.GROQ_API_KEY:
    logger.warning("GROQ_API_KEY is not set in the environment variables or .env file!")

