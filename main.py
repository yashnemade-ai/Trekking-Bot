
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="TrekMate AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

client = genai.Client(api_key=API_KEY) if API_KEY else None

SYSTEM_PROMPT = """
You are TrekMate AI, a friendly and knowledgeable trekking assistant.
Help with trekking routes, gear, fitness preparation, safety, permits,
budgets, itineraries, and seasons, especially for Indian Himalayan treks.
Reply in the same language as the user, including Hindi or English.
Give practical, clear answers. Never invent live weather or claim
current trail conditions have been verified when they have not.
For dangerous conditions, prioritize safety and official local guidance.
"""

class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)

@app.get("/")
async def home():
    index_file = Path(__file__).parent / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "TrekMate AI API is running", "docs": "/docs"}

@app.get("/health")
async def health():
    if not API_KEY or client is None:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is missing in environment variables."
        )

    try:
        info = client.models.get(model=MODEL_NAME)
        return {
            "status": "ok",
            "model": info.name or MODEL_NAME,
            "message": "Gemini model is accessible."
        }
    except Exception as exc:
        logger.exception("Gemini health check failed")
        raise HTTPException(
            status_code=502,
            detail=f"Gemini model check failed: {str(exc)[:500]}"
        )

@app.get("/models")
async def models():
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is missing.")

    try:
        result = client.models.list()
        available = []
        for model in result:
            methods = getattr(model, "supported_actions", None) or []
            if "generateContent" in methods:
                available.append(model.name)
        return {"models": available}
    except Exception as exc:
        logger.exception("Could not list models")
        raise HTTPException(status_code=502, detail=str(exc)[:500])

@app.post("/chat")
async def chat(request: ChatRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please enter a question.")

    # Keep only valid, recent user/assistant messages.
    contents = []
    for item in request.history[-12:]:
        role = item.get("role")
        message = item.get("content", "").strip()
        if role in ("user", "assistant") and message:
            contents.append(
                types.Content(
                    role="user" if role == "user" else "model",
                    parts=[types.Part.from_text(text=message[:4000])]
                )
            )

    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=question)]
        )
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=1200,
            ),
        )

        answer = response.text
        if not answer or not answer.strip():
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty response. Try again."
            )

        return {"answer": answer.strip(), "model": MODEL_NAME}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat generation failed")

        message = str(exc)
        if "404" in message or "NOT_FOUND" in message:
            detail = (
                f"Model '{MODEL_NAME}' is unavailable. Check GET /models "
                "and set GEMINI_MODEL to a listed model."
            )
        elif "429" in message or "RESOURCE_EXHAUSTED" in message:
            detail = "Gemini API quota or rate limit reached. Try again later."
        elif "403" in message or "PERMISSION_DENIED" in message:
            detail = "Gemini API access denied. Check your API key and permissions."
        else:
            detail = f"Gemini request failed: {message[:400]}"

        raise HTTPException(status_code=502, detail=detail)
