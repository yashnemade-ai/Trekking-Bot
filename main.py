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

# ⭐ Comma-separated list of models to try in order.
#    Each model is attempted at most once per request.
DEFAULT_MODELS = "gemini-2.0-flash,gemini-2.5-flash,gemini-1.5-flash,gemini-1.5-flash-8b"
GEMINI_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", DEFAULT_MODELS).split(",")
    if m.strip()
]

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
UNSPLASH_ENABLED = bool(UNSPLASH_ACCESS_KEY and UNSPLASH_ACCESS_KEY.strip())

client = genai.Client(api_key=API_KEY) if API_KEY else None


# ═══════════════════════════════════════════════
#  SYSTEM PROMPT — Trekking-only assistant
# ═══════════════════════════════════════════════
SYSTEM_PROMPT = """
You are TrekMate AI, a friendly and knowledgeable trekking assistant.

SCOPE — VERY IMPORTANT:
- You ONLY answer questions about TREKKING and HIKING.
- This includes: trekking destinations, routes, difficulty, altitude, seasons,
  packing lists, gear, fitness preparation, safety, permits, budgets,
  camping, food, water, itineraries, and mountain travel.
- If the user asks ANYTHING unrelated to trekking (politics, coding, movies,
  general knowledge, non-trek travel, medicine, finance, etc.), politely
  decline and redirect them back to trekking. Keep the redirect short,
  friendly, and in the same language as the user. Example:
  "Main sirf trekking se related sawaalon mein madad kar sakta hoon. Kisi
  trek, route, gear, ya season ke baare mein poochhein."

TRUTHFULNESS:
- Never invent live weather, current trail conditions, or verified facts.
- If you are not sure, say so and suggest checking with local authorities.
- For dangerous conditions, prioritize safety and official local guidance.

LANGUAGE:
- Reply in the same language the user writes in (Hindi, English, Hinglish, etc.).

FORMATTING RULES (very important):
- Write in plain text. Do NOT use hashtags (#), asterisks (*), backticks (`),
  or any markdown symbols except for **bold**.
- Use double asterisks only for section headings or key labels, e.g.
  **Difficulty** or **Best Season**. These will be rendered as bold on the client.
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
#  HELPERS
# ═══════════════════════════════════════════════
def _build_contents(question: str, history: list[dict[str, str]]):
    contents = []
    for item in history[-12:]:
        role = item.get("role")
        message = (item.get("content") or "").strip()
        if role in ("user", "assistant") and message:
            contents.append(
                types.Content(
                    role="user" if role == "user" else "model",
                    parts=[types.Part.from_text(text=message[:4000])],
                )
            )
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=question)],
        )
    )
    return contents


def _is_retryable_error(exc: Exception) -> bool:
    """
    Return True if the error is a quota / rate-limit / model-missing /
    temporary server error — meaning we should try the NEXT model.
    """
    msg = str(exc).upper()
    retryable_markers = (
        "429",
        "RESOURCE_EXHAUSTED",
        "QUOTA",
        "RATE",
        "RATE_LIMIT",
        "404",
        "NOT_FOUND",
        "NOT FOUND",
        "UNAVAILABLE",
        "503",
        "502",
        "500",
        "TIMEOUT",
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "OVERLOADED",
    )
    return any(marker in msg for marker in retryable_markers)


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
            detail="GEMINI_API_KEY is missing in environment variables.",
        )
    try:
        available = []
        for name in GEMINI_MODELS:
            try:
                info = client.models.get(model=name)
                available.append(info.name or name)
            except Exception as exc:
                logger.warning("Health check: model %s unavailable: %s", name, str(exc)[:120])
                available.append(f"{name} (unavailable)")

        return {
            "status": "ok",
            "configured_models": GEMINI_MODELS,
            "checked_models": available,
            "unsplash": UNSPLASH_ENABLED,
            "message": "TrekMate AI is running.",
        }
    except Exception as exc:
        logger.exception("Gemini health check failed")
        raise HTTPException(status_code=502, detail=f"Health check failed: {str(exc)[:500]}")


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
        return {"configured": GEMINI_MODELS, "available": available}
    except Exception as exc:
        logger.exception("Could not list models")
        raise HTTPException(status_code=502, detail=str(exc)[:500])


# ═══════════════════════════════════════════════
#  UNSPLASH IMAGE PROXY
# ═══════════════════════════════════════════════
@app.get("/images")
async def images(
    q: str = Query(..., min_length=2, max_length=100, description="Search query"),
    count: int = Query(4, ge=1, le=12, description="Number of images"),
):
    if not UNSPLASH_ENABLED:
        return {"images": [], "reason": "Unsplash not configured on server."}

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
#  CHAT — Multi-model with fallback
# ═══════════════════════════════════════════════
@app.post("/chat")
async def chat(request: ChatRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please enter a question.")

    if not GEMINI_MODELS:
        raise HTTPException(
            status_code=500,
            detail="No Gemini models configured. Set GEMINI_MODELS env variable.",
        )

    contents = _build_contents(question, request.history)

    last_error: Optional[str] = None
    used_model: Optional[str] = None
    answer: Optional[str] = None

    for model_name in GEMINI_MODELS:
        try:
            logger.info("Trying model: %s", model_name)

            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=1200,
                ),
            )

            text = (response.text or "").strip()

            if not text:
                logger.warning("Model %s returned empty text — trying next.", model_name)
                last_error = f"Model '{model_name}' returned empty response."
                continue

            answer = text
            used_model = model_name
            logger.info("Success with model: %s", model_name)
            break

        except Exception as exc:
            err_msg = str(exc)
            logger.warning("Model %s failed: %s", model_name, err_msg[:250])

            if _is_retryable_error(exc):
                last_error = err_msg
                continue
            else:
                # Non-retryable (bad key, auth error, etc.) — stop and surface it.
                last_error = err_msg
                break

    if not answer:
        # All models failed — return a clear user-friendly error.
        if last_error and ("429" in last_error or "RESOURCE_EXHAUSTED" in last_error.upper()):
            detail = "All Gemini models are rate-limited right now. Please try again in a few minutes."
        elif last_error and ("404" in last_error or "NOT_FOUND" in last_error.upper()):
            detail = "Configured Gemini models are unavailable. Check GET /models and update GEMINI_MODELS."
        elif last_error and ("403" in last_error or "PERMISSION_DENIED" in last_error.upper()):
            detail = "Gemini API access denied. Check your API key and permissions."
        else:
            detail = f"All Gemini models failed. Last error: {(last_error or 'unknown')[:300]}"
        raise HTTPException(status_code=502, detail=detail)

    # ⭐ Image search (independent from Gemini success path)
    images_payload = []
    image_keywords = (
        "image", "images", "photo", "photos", "picture", "pictures",
        "show me", "dikhao", "tasveer", "trek", "trekking",
        "himalaya", "mountain", "waterfall", "camp", "trail",
        "kedarkantha", "hampta", "sinhagad", "valley", "peak",
        "pass", "fort", "base camp", "lake", "glacier",
    )
    wants_images = any(k in question.lower() for k in image_keywords)

    if UNSPLASH_ENABLED and wants_images:
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
                        credit_link = user_links.get("html") or "https://unsplash.com"
                        sep = "&" if "?" in credit_link else "?"
                        images_payload.append({
                            "url": image_url,
                            "alt": (
                                img.get("alt_description")
                                or img.get("description")
                                or "Trekking photo"
                            ),
                            "credit": user.get("name") or "Unsplash",
                            "link": (
                                credit_link + sep
                                + "utm_source=trekmate"
                                + "&utm_medium=referral"
                            ),
                        })
            else:
                logger.warning("Unsplash search failed: HTTP %s", res.status_code)
        except Exception:
            logger.exception("Failed to fetch chat images")

    return {
        "answer": answer,
        "model": used_model,
        "images": images_payload,
    }
