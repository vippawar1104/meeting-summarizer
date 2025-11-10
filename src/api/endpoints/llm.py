# src/api/endpoints/llm.py
from fastapi import APIRouter, HTTPException, status, Depends

from schemas.llm import (
    LLMRequestBase,
    SummarizationResponse,
    ActionItemsResponse,
    ChatRequest, ChatResponse
)
from services import llm_service
from core.config import logger

router = APIRouter()

# Dependency check
async def check_llm_availability():
    if llm_service.llm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service is not configured or unavailable. Check GROQ_API_KEY.",
        )


@router.post(
    "/summarize",
    response_model=SummarizationResponse,
    summary="Generate Meeting Summary",
    description="Receives transcript text and returns ONLY a concise summary using Groq LLM.",
    tags=["LLM Features"],
    dependencies=[Depends(check_llm_availability)]
)
async def summarize_endpoint(request: LLMRequestBase):
    if not request.transcript:
         raise HTTPException(status_code=400, detail="Transcript text cannot be empty.")
    try:
        logger.info("Received request for summarization")
        result = await llm_service.generate_summary(request.transcript)
        return result
    except ValueError as ve:
        logger.warning(f"Summarization validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except ConnectionError as ce:
         logger.error(f"Summarization connection error: {ce}")
         raise HTTPException(status_code=503, detail=str(ce))
    except RuntimeError as re:
         logger.error(f"Summarization runtime error: {re}")
         raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        logger.exception("Unhandled exception during summarization")
        raise HTTPException(status_code=500, detail="An internal server error occurred during summarization.")

@router.post(
    "/extract-action-items",
    response_model=ActionItemsResponse,
    summary="Extract Action Items",
    description="Receives transcript text and returns ONLY a list of extracted action items using Groq LLM.",
    tags=["LLM Features"],
    dependencies=[Depends(check_llm_availability)]
)
async def extract_action_items_endpoint(request: LLMRequestBase):
    if not request.transcript:
        raise HTTPException(status_code=400, detail="Transcript text cannot be empty.")
    try:
        logger.info("Received request for action item extraction")
        result = await llm_service.extract_action_items(request.transcript)
        return result
    except ValueError as ve:
        logger.warning(f"Action item validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except ConnectionError as ce:
        logger.error(f"Action item connection error: {ce}")
        raise HTTPException(status_code=503, detail=str(ce))
    except RuntimeError as re:
        logger.error(f"Action item runtime error: {re}")
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        logger.exception("Unhandled exception during action item extraction")
        raise HTTPException(status_code=500, detail="An internal server error occurred during action item extraction.")


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat about Meeting Transcript",
    description="Answers a user's question based on the provided transcript context using Groq LLM.",
    tags=["LLM Features"],
    dependencies=[Depends(check_llm_availability)]
)
async def chat_endpoint(request: ChatRequest):
    if not request.transcript_context or not request.user_query:
        raise HTTPException(status_code=400, detail="Transcript context and user query are required.")
    try:
        logger.info(f"Received chat query: '{request.user_query[:50]}...'")
        result = await llm_service.answer_query(request.transcript_context, request.user_query)
        return result
    except ValueError as ve:
        logger.warning(f"Chat validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except ConnectionError as ce:
        logger.error(f"Chat connection error: {ce}")
        raise HTTPException(status_code=503, detail=str(ce))
    except RuntimeError as re:
        logger.error(f"Chat runtime error: {re}")
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        logger.exception("Unhandled exception during chat")
        raise HTTPException(status_code=500, detail="An internal server error occurred during chat.")
