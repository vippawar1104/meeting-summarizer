# src/api/endpoints/transcription.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks

from src.schemas.transcription import TranscriptionResponse
from src.services.transcription_service import process_audio_file
from src.core.config import logger

router = APIRouter()

# Define the list of acceptable MIME types for the OpenAPI spec
ACCEPTED_MEDIA_TYPES = [
    "audio/mpeg", # .mp3
    "audio/wav",  # .wav
    "audio/x-wav", # .wav
    "audio/mp4",  # .mp4, .m4a (audio part)
    "audio/ogg",  # .ogg, .opus
    "audio/webm", # .webm (audio part)
    "video/mp4",  # .mp4 (AssemblyAI can extract audio)
    "video/webm", # .webm (AssemblyAI can extract audio)
    "video/quicktime", # .mov
    "video/x-matroska", # .mkv
]

# More direct attempt via simple description often used:
openapi_file_description = f"The audio/video file to transcribe. Accepted types: {', '.join(ACCEPTED_MEDIA_TYPES)}"


@router.post(
    "/transcribe",
    response_model=TranscriptionResponse,
    summary="Transcribe Audio File",
    description="Upload an audio/video file to transcribe it using AssemblyAI. "
                "Returns the transcript text, detected language, and speaker-separated utterances.",
    tags=["Transcription"],
)
async def transcribe_audio_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description=openapi_file_description)
):
    """
    Endpoint to receive an audio file and initiate transcription.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file provided.")

    # SERVER-SIDE VALIDATION
    if file.content_type not in ACCEPTED_MEDIA_TYPES:
        logger.warning(f"Received file type '{file.content_type}' not in explicitly allowed list, but attempting processing.")
        raise HTTPException(
            status_code=415, # Unsupported Media Type
            detail=f"Unsupported file type: {file.content_type}. Allowed: {', '.join(ACCEPTED_MEDIA_TYPES)}"
        )

    logger.info(f"Received file for transcription: {file.filename}, type: {file.content_type}")

    try:
        result = await process_audio_file(file, background_tasks)
        if result.status == 'error':
            raise HTTPException(status_code=422, detail=f"Transcription failed: {result.error}")
        return result
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.exception(f"Unhandled exception in /transcribe endpoint for file {file.filename}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")

