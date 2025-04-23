# src/schemas/llm.py
from pydantic import BaseModel, Field
from typing import List

# Request Schemas
class LLMRequestBase(BaseModel):
    """Base request needing transcript text."""
    transcript: str = Field(..., description="The full transcript text.")

# Response Schemas 
class SummarizationResponse(BaseModel):
    """Response schema for the summarization endpoint."""
    summary: str = Field(..., description="The generated concise summary of the meeting.")

class ActionItemsResponse(BaseModel):
    """Response schema for the action items endpoint."""
    action_items: List[str] = Field(default_factory=list, description="Action items extracted from the transcript.")

class ChatRequest(BaseModel):
    transcript_context: str = Field(..., description="The transcript context for the chat.")
    user_query: str = Field(..., description="The user's question.")

class ChatResponse(BaseModel):
    ai_response: str = Field(..., description="The AI's answer to the user's query.")

