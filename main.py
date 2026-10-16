import os
import logging
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
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

# ═══════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════
API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
UNSPLASH_ENABLED = bool(UNSPLASH_ACCESS_KEY and UNSPLASH_ACCESS_KEY.strip())

client = genai.Client(api_key=API_KEY) if API_KEY else None

SYSTEM_PROMPT = """
You are TrekMate AI, a friendly and knowledgeable trekking assistant.
Help with trekking routes, gear, fitness preparation, safety, permits,
budgets, itineraries, and seasons, especially for Indian Himalayan treks.
Reply in the same language as the user, including Hindi or English.
Give practical, clear answers. Never invent live weather or claim
current trail conditions have been verified when they have not.
For dangerous conditions, prioritize safety and official local guidance.

FORMATTING RULES (very important):
- Write in plain text. Do NOT use hashtags (#), asterisks (*), backticks (`),
  or any markdown symbols except for **bold**.
- Use double asterisks only for section headings or key labels, e.g. **Difficulty**
  or **Best Season**. These will be rendered as bold on the client.
- For bullet lists, use simple dashes (-) at the start of a line.
- Do NOT add leading spaces or tabs at the beginning of lines.
- Do NOT use numbered sections like "1." or "2." unless the user asks.
- Separate sections with a single blank line, never multiple blank lines.
- Do NOT wrap the whole answer in code blocks.
- Keep answers concise and mobile-friendly.
"""


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


# ═══════════════════════════════════════════════
#  FRONTEND
# ═══════════════════════════════════════════════
@app.get("/")
async def home():
    index_file = Path(__file__).parent / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "TrekMate AI API is running", "docs": "/docs"}


# ═══════════════════════════════════════════════
#  HEALTH
# ═══════════════════════════════════════════════
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
            "unsplash": UNSPLASH_ENABLED,
            "message": "TrekMate AI is running."
        }
    except Exception as exc:
        logger.exception("Gemini health check failed")
        raise HTTPException(status_code=502, detail=f"Gemini model check failed: {str(exc)[:500]}")


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


# ═══════════════════════════════════════════════
#  ⭐ UNSPLASH IMAGE PROXY
#  Keeps the API key SAFE on the server.
#  Frontend calls: GET /images?q=kedarkantha+trek&count=4
# ═══════════════════════════════════════════════
@app.get("/images")
async def images(
    q: str = Query(..., min_length=2, max_length=100, description="Search query"),
    count: int = Query(4, ge=1, le=12, description="Number of images"),
):
    if not UNSPLASH_ENABLED:
        # Return empty list gracefully if Unsplash not configured.
        return {"images": [], "reason": "Unsplash not configured on server."}

    # Sanitize query
    search_query = q.strip()
    if not search_query:
        return {"images": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            res = await http.get(
                "https://api.unsplash.com/search/photos",
                params={
                    "query": search_query,
                    "per_page": count,
                    "orientation": "landscape",
                    "content_filter": "high",
                },
                headers={
                    "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
                    "Accept-Version": "v1",
                },
            )

        if res.status_code == 401:
            logger.warning("Unsplash: invalid access key")
            raise HTTPException(status_code=401, detail="Unsplash access key invalid.")
        if res.status_code == 403:
            raise HTTPException(status_code=403, detail="Unsplash forbidden.")
        if res.status_code == 429:
            raise HTTPException(status_code=429, detail="Unsplash rate limit reached. Try again later.")

        if res.status_code != 200:
            logger.warning("Unsplash error: %s %s", res.status_code, res.text[:200])
            raise HTTPException(status_code=502, detail=f"Unsplash returned {res.status_code}")

        data = res.json()
        results = data.get("results", [])

        payload = []
        for img in results:
            user = img.get("user") or {}
            user_links = user.get("links") or {}
            payload.append({
                "id":         img.get("id"),
                "thumb":      (img.get("urls") or {}).get("small"),
                "full":       (img.get("urls") or {}).get("regular"),
                "alt":        img.get("alt_description") or img.get("description") or "Trek image",
                "credit":     user.get("name") or "Unsplash",
                "creditLink": (user_links.get("html") or "https://unsplash.com") +
                              "?utm_source=trekmate&utm_medium=referral",
            })

        return {"images": payload}

    except HTTPException:
        raise
    except httpx.TimeoutException:
        logger.warning("Unsplash request timed out")
        raise HTTPException(status_code=504, detail="Unsplash request timed out.")
    except Exception as exc:
        logger.exception("Unsplash proxy failed")
        raise HTTPException(status_code=502, detail=f"Image fetch failed: {str(exc)[:200]}")


# ═══════════════════════════════════════════════
#  CHAT
# ═══════════════════════════════════════════════
@app.post("/chat")
async def chat(request: ChatRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please enter a question.")

    contents = []
    for item in request.history[-12:]:
        role = item.get("role")
        message = (item.get("content") or "").strip()
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

        answer = (response.text or "").strip()
        if not answer:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty response. Try again."
            )

        
        images_payload = []

        # Search for trekking photos when relevant
        image_keywords = (
            "image", "images", "photo", "photos", "picture", "pictures",
            "show me", "dikhao", "tasveer", "trek", "trekking",
            "himalaya", "mountain", "waterfall", "camp", "trail",
            "kedarkantha", "hampta", "sinhagad", "valley", "peak"
        )

        if UNSPLASH_ENABLED and any(
            keyword in question.lower() for keyword in image_keywords
        ):
            try:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    res = await http.get(
                        "https://api.unsplash.com/search/photos",
                        params={
                            "query": question[:100],
                            "per_page": 4,
                            "orientation": "landscape",
                            "content_filter": "high",
                        },
                        headers={
                            "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
                            "Accept-Version": "v1",
                        },
                    )

                if res.status_code == 200:
                    for img in res.json().get("results", []):
                        urls = img.get("urls") or {}
                        user = img.get("user") or {}
                        user_links = user.get("links") or {}

                        image_url = urls.get("regular")
                        if image_url:
                            credit_link = (
                                user_links.get("html") or "https://unsplash.com"
                            )
                            separator = "&" if "?" in credit_link else "?"
                            images_payload.append({
                                "url": image_url,
                                "alt": (
                                    img.get("alt_description")
                                    or img.get("description")
                                    or "Trekking photo"
                                ),
                                "credit": user.get("name") or "Unsplash",
                                "link": (
                                    credit_link + separator
                                    + "utm_source=trekmate"
                                    + "&utm_medium=referral"
                                ),
                            })
                else:
                    logger.warning(
                        "Unsplash search failed: HTTP %s", res.status_code
                    )

            except Exception:
                logger.exception("Failed to fetch chat images")

        return {
            "answer": answer,
            "model": MODEL_NAME,
            "images": images_payload,
        }


    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat generation failed")
        message = str(exc)
        if "404" in message or "NOT_FOUND" in message:
            detail = f"Model '{MODEL_NAME}' is unavailable. Check GET /models."
        elif "429" in message or "RESOURCE_EXHAUSTED" in message:
            detail = "Gemini API quota or rate limit reached. Try again later."
        elif "403" in message or "PERMISSION_DENIED" in message:
            detail = "Gemini API access denied. Check your API key."
        else:
            detail = f"Gemini request failed: {message[:400]}"
        raise HTTPException(status_code=502, detail=detail)
