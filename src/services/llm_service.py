# src/services/llm_service.py
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, PydanticOutputParser
from langchain_core.exceptions import OutputParserException

from src.core.config import settings, logger
from src.schemas.llm import SummarizationResponse, ActionItemsResponse, ChatResponse

# Initialize Groq LLM
if not settings.GROQ_API_KEY:
    logger.error("GROQ_API_KEY not found. Cannot initialize LLM Service.")
    llm = None
else:
    try:
        llm = ChatGroq(
            temperature=0.1,
            groq_api_key=settings.GROQ_API_KEY,
            model_name=settings.GROQ_MODEL_NAME,
            max_retries=2,
        )
        logger.info(f"ChatGroq LLM initialized with model: {settings.GROQ_MODEL_NAME}")
    except Exception as e:
        logger.error(f"Failed to initialize ChatGroq: {e}", exc_info=True)
        llm = None

# Prompt Templates
SUMMARY_ONLY_PROMPT_TEMPLATE = """System: You are an expert meeting assistant. Your task is to analyze the provided transcript and generate ONLY a concise summary of the main discussion points, key decisions, and overall outcome. Focus on clarity and accuracy.
\n---
Transcript Context:
{context}
---\n
Human: Based on the transcript provided, generate the concise summary.
Assistant:"""
summary_only_prompt = ChatPromptTemplate.from_template(SUMMARY_ONLY_PROMPT_TEMPLATE)

action_items_parser = PydanticOutputParser(pydantic_object=ActionItemsResponse)

ACTION_ITEMS_PROMPT_TEMPLATE_PYDANTIC = """System: You are an expert meeting assistant focusing ONLY on identifying action items from the provided transcript.
Extract specific, concrete tasks or actions assigned during the meeting. Include the owner if mentioned.
Format your response as a JSON object according to the following schema.
If NO specific action items are identified, return a JSON object with an empty list for the 'action_items' field.

{format_instructions}

---
Transcript Context:
{context}
---

Human: Based *only* on the transcript provided, extract all specific action items and format them as JSON according to the schema.
Assistant:""" # Removed NO_ACTION_ITEMS string instruction

action_items_prompt_pydantic = ChatPromptTemplate.from_template(
    ACTION_ITEMS_PROMPT_TEMPLATE_PYDANTIC,
    partial_variables={"format_instructions": action_items_parser.get_format_instructions()}
)

CHAT_PROMPT_TEMPLATE = """System: You are an AI assistant answering questions based *only* on the provided meeting transcript context. Be concise and directly address the user's query using information from the transcript. If the answer cannot be found in the transcript, explicitly state "The answer is not available in the provided transcript context." Do not make assumptions or use external knowledge.
\n---
Meeting Transcript Context:
{transcript_context}
---\n
Human: {user_query}
Assistant:"""
chat_prompt = ChatPromptTemplate.from_template(CHAT_PROMPT_TEMPLATE)


# Service Functions
async def generate_summary(text: str) -> SummarizationResponse:
    """Generates ONLY the summary from the transcript."""
    if not llm:
        raise ConnectionError("LLM service is not available.")
    if not text:
        raise ValueError("Transcript cannot be empty.")

    logger.info(f"Requesting summary from model {settings.GROQ_MODEL_NAME}")
    logger.debug(f"Transcript length: {len(text)} characters")
    try:
        chain = summary_only_prompt | llm | StrOutputParser()
        logger.debug("Calling LLM chain with input...")
        summary_text = await chain.ainvoke({"context": text})
        logger.info("Summary LLM call successful.")
        return SummarizationResponse(summary=summary_text.strip())
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Summary generation failed: {e}")
        logger.error(tb)
        raise RuntimeError(f"Failed to generate summary: {e}")


async def extract_action_items(text: str) -> ActionItemsResponse:
    """Extracts ONLY the action items using PydanticOutputParser."""
    if not llm:
        raise ConnectionError("LLM service is not available.")
    if not text:
        raise ValueError("Transcript cannot be empty.")

    logger.info(f"Requesting action items extraction")
    try:
        # Chain now uses the pydantic prompt and parser
        chain = action_items_prompt_pydantic | llm | action_items_parser

        # The result of invoke IS the parsed Pydantic object
        parsed_output: ActionItemsResponse = await chain.ainvoke({"context": text})
        logger.info("Action items LLM call and parsing successful.")

        # Optional: Filter out any empty strings the LLM might have included
        if parsed_output and parsed_output.action_items:
            parsed_output.action_items = [item for item in parsed_output.action_items if item and item.strip()]

        # Add model used before returning
        return parsed_output

    except OutputParserException as ope:
        logger.error(f"Failed to parse LLM output for action items: {ope}", exc_info=True)
        raise RuntimeError(f"Failed to parse action items from LLM response: {ope}")
    except Exception as e:
        logger.error(f"Action item extraction failed: {e}", exc_info=True)
        raise RuntimeError(f"Failed to extract action items: {e}")


async def answer_query(transcript_context: str, user_query: str) -> ChatResponse:
    """Answers a user query based on the provided transcript context."""
    if not llm:
        raise ConnectionError("LLM service is not available.")
    if not transcript_context or not user_query:
        raise ValueError("Transcript context and user query cannot be empty.")

    logger.info(f"Requesting chat response from model {settings.GROQ_MODEL_NAME}")
    try:
        chain = chat_prompt | llm | StrOutputParser()
        result = await chain.ainvoke({
            "transcript_context": transcript_context,
            "user_query": user_query
        })
        logger.info("Chat LLM call successful.")
        return ChatResponse(ai_response=result.strip())
    except Exception as e:
        logger.error(f"Chat query failed: {e}", exc_info=True)
        raise RuntimeError(f"Failed to get chat response: {e}")
