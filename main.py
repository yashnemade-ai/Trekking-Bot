urls = photo.get("urls", {})
            user = photo.get("user", {})
            links = photo.get("links", {})
            image_url = urls.get("regular") or urls.get("small")
            if image_url:
                images.append({
                    "url": image_url,
                    "alt": photo.get("alt_description") or photo.get("description") or query,
                    "credit": user.get("name", "Unsplash contributor"),
                    "link": links.get("html", "https://unsplash.com"),
                })
        return images
    except Exception:
        logger.exception("Unsplash image search failed")
        return []

@app.get("/")
async def home():
    index_file = Path(__file__).parent / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "TrekMate AI API is running", "docs": "/docs"}

@app.get("/health")
async def health():
    if not API_KEY or client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is missing in environment variables.")
    try:
        info = client.models.get(model=MODEL_NAME)
        return {"status": "ok", "model": info.name or MODEL_NAME,
                "unsplash_configured": bool(UNSPLASH_ACCESS_KEY)}
    except Exception as exc:
        logger.exception("Gemini health check failed")
        raise HTTPException(status_code=502, detail=f"Gemini model check failed: {str(exc)[:400]}")

@app.get("/models")
def models():
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is missing.")
    try:
        result = client.models.list()
        available = [m.name for m in result if "generateContent" in (getattr(m, "supported_actions", None) or [])]
        return {"models": available}
    except Exception as exc:
        logger.exception("Could not list models")
        raise HTTPException(status_code=502, detail=str(exc)[:400])

@app.post("/chat")
def chat(request: ChatRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please enter a question.")

    contents = []
    for item in request.history[-10:]:
        role = item.get("role")
        message = (item.get("content") or "").strip()
        if role in ("user", "assistant") and message:
            contents.append(types.Content(
                role="user" if role == "user" else "model",
                parts=[types.Part.from_text(text=message[:4000])]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, max_output_tokens=1200),
        )
        answer = (response.text or "").strip()
        if not answer:
            raise HTTPException(status_code=502, detail="Gemini returned an empty response. Try again.")

        # Attach photos for trekking/place queries. The key stays on the server.
        q_lower = question.lower()
        image_intent = any(word in q_lower for word in [
            "image", "images", "photo", "photos", "picture", "pictures", "show me", "show images", "dikhao", "photo dikhao", "tasveer"
        ])
        trek_context = any(word in q_lower for word in [
            "trek", "trekking", "mountain", "himalaya", "trail", "camp", "waterfall", "fort", "valley", "peak", "sinhagad", "kedarkantha", "hampta", "flowers"
        ])
        images = []
        if image_intent or trek_context:
            # Use the question to make search relevant, but remove generic request wording.
            query = question[:160] + " trekking landscape"
            images = search_unsplash(query, 4)
            if not images and trek_context:
                images = search_unsplash("Indian Himalayan trekking mountains trail", 4)
        return {"answer": answer, "model": MODEL_NAME, "images": images}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat generation failed")
        message = str(exc)
        if "404" in message or "NOT_FOUND" in message:
            detail = f"Model '{MODEL_NAME}' is unavailable. Check GET /models and set GEMINI_MODEL to an available model."
        elif "429" in message or "RESOURCE_EXHAUSTED" in message:
            detail = "Gemini API quota or rate limit reached. Try again later."
        elif "503" in message or "UNAVAILABLE" in message:
            detail = "Gemini is temporarily busy. Please try again shortly."
        elif "403" in message or "PERMISSION_DENIED" in message:
            detail = "Gemini API access denied. Check your API key and permissions."
        else:
            detail = f"Gemini request failed: {message[:400]}"
        raise HTTPException(status_code=502, detail=detail)
