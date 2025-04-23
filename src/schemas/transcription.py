# src/schemas/transcription.py
from pydantic import BaseModel
from typing import List, Optional

class Utterance(BaseModel):
    """Schema for a single utterance with speaker label."""
    speaker: Optional[str] = None # Speaker label (e.g., 'A', 'B')
    start: int # Start time in milliseconds
    end: int # End time in milliseconds
    text: str
    confidence: float

class TranscriptionResponse(BaseModel):
    """Response schema for the transcription endpoint."""
    status: str # e.g., 'completed', 'error'
    transcript_id: str
    text: Optional[str] = None # Full transcript text
    language_code: Optional[str] = None # Detected language
    utterances: Optional[List[Utterance]] = None # Speaker-separated utterances
    error: Optional[str] = None # Error message if status is 'error'

