# src/services/transcription_service.py
import assemblyai as aai
import os
import uuid
import shutil
from fastapi import UploadFile, HTTPException, BackgroundTasks

from src.core.config import settings, logger
from src.schemas.transcription import TranscriptionResponse, Utterance

# Configure AssemblyAI SDK
if settings.ASSEMBLYAI_API_KEY:
    aai.settings.api_key = settings.ASSEMBLYAI_API_KEY
else:
    logger.error("AssemblyAI API Key not configured. Transcription service will not work.")
    raise ValueError("AssemblyAI API Key is required but not configured.")

def cleanup_file(filepath: str):
    """Removes a file safely."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up temporary file: {filepath}")
    except OSError as e:
        logger.error(f"Error cleaning up file {filepath}: {e}")

async def process_audio_file(
    file: UploadFile,
    background_tasks: BackgroundTasks
) -> TranscriptionResponse:
    """
    Saves the uploaded audio file temporarily, submits it to AssemblyAI for transcription
    with speaker labels and language detection, and returns the results.
    """
    if not aai.settings.api_key:
        raise HTTPException(status_code=503, detail="Transcription service is unavailable due to missing API key.")

    _, file_extension = os.path.splitext(file.filename or "audio.tmp")
    temp_filename = f"{uuid.uuid4()}{file_extension}"
    temp_filepath = os.path.join(settings.UPLOAD_DIR, temp_filename)

    try:
        with open(temp_filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"Temporary file saved: {temp_filepath}")
    except Exception as e:
        logger.error(f"Could not save temporary file: {e}")
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}")
    finally:
        await file.close()

    background_tasks.add_task(cleanup_file, temp_filepath)

    config = aai.TranscriptionConfig(
        speaker_labels=True,      # Enable speaker diarization
        language_detection=True   # Enable language detection (for multilingual)
    )
    transcriber = aai.Transcriber(config=config)

    logger.info(f"Submitting file for transcription: {temp_filepath}")
    try:
        transcript = transcriber.transcribe(temp_filepath)
        logger.info(f"Transcription completed for ID: {transcript.id}")

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"Transcription failed: {transcript.error}")
            return TranscriptionResponse(
                status=str(transcript.status),
                transcript_id=transcript.id,
                error=transcript.error
            )

        utterance_list = []
        if transcript.utterances:
            utterance_list = [
                Utterance(
                    speaker=utt.speaker,
                    start=utt.start,
                    end=utt.end,
                    text=utt.text,
                    confidence=utt.confidence
                ) for utt in transcript.utterances
            ]

        return TranscriptionResponse(
            status=str(transcript.status),
            transcript_id=transcript.id,
            text=transcript.text,
            language_code=getattr(transcript, 'language_code', None),
            utterances=utterance_list,
            error=transcript.error # Should be None if status is completed
        )

    except Exception as e:
        logger.error(f"An unexpected error occurred during transcription: {e}", exc_info=True)
        # Attempt cleanup immediately if an exception occurs before background task runs
        cleanup_file(temp_filepath)
        raise HTTPException(status_code=500, detail=f"Transcription process failed: {e}")

