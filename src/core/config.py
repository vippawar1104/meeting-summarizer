# src/core/config.py
import os
from pydantic_settings import BaseSettings
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), '../../.env')
load_dotenv(dotenv_path)

class Settings(BaseSettings):
    """Application settings."""
    APP_NAME: str = "Multilingual Note-Taking Agent API"
    API_V1_STR: str = "/api/v1"
    ASSEMBLYAI_API_KEY: str = os.getenv("ASSEMBLYAI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL_NAME: str = os.getenv("GROQ_MODEL_NAME", "llama-3.3-70b-versatile")

    # LangChain specific settings (optional, for text splitting)
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "4000"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "200"))

    # Temporary directory for uploads (using /tmp in serverless environment)
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "/tmp/audio_uploads")

    class Config:
        case_sensitive = True
        env_file = '.env'
        env_file_encoding = 'utf-8'

settings = Settings()

# Basic logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create upload directory only if we're not in a serverless environment
if not os.getenv("VERCEL"):
    try:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    except Exception as e:
        logger.warning(f"Could not create upload directory: {e}")

logger.info("Settings loaded.")
if not settings.ASSEMBLYAI_API_KEY:
    logger.warning("ASSEMBLYAI_API_KEY is not set in the environment variables or .env file!")
if not settings.GROQ_API_KEY:
    logger.warning("GROQ_API_KEY is not set in the environment variables or .env file!")

