from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from google import genai
from google.genai import types
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Render dashboard mein GEMINI_API_KEY set hona chahiye
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable set nahi hai!")

client = genai.Client(api_key=GEMINI_API_KEY)

# ✅ Sahi model name
MODEL_NAME = "gemini-1.5-flash"   # stable, fast, free tier mein available

# ✅ System instruction (optional but recommended)
SYSTEM_PROMPT = (
    "You are TrekMate AI, a helpful and knowledgeable trekking assistant. "
    "You help users with trekking routes, gear, safety tips, weather, "
    "permits, and best seasons for treks (especially Indian Himalayas). "
    "Be friendly, concise, and practical. Reply in the same language "
    "the user writes in (Hindi or English)."
)


class ChatRequest(BaseModel):
    question: str


@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>TrekMate AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .chat-window { height: 75vh; }
            .msg { max-width: 80%; margin-bottom: 10px; padding: 12px; border-radius: 15px; }
        </style>
    </head>
    <body class="bg-slate-100 flex flex-col items-center p-4 font-sans">
        <div class="w-full max-w-2xl bg-white shadow-2xl rounded-2xl overflow-hidden mt-5 border border-slate-200">
            <div class="bg-emerald-600 p-4 text-white text-xl font-bold text-center shadow-md">🥾 TrekMate Assistant</div>
            <div id="box" class="chat-window p-4 overflow-y-auto flex flex-col bg-slate-50">
                <div class="bg-slate-200 text-slate-800 msg self-start shadow-sm">Namaste! Main TrekMate AI hoon. Main aapki kya madad kar sakta hoon?</div>
            </div>
            <div class="p-4 flex gap-2 bg-white border-t">
                <input type="text" id="userInput" class="flex-1 p-3 border rounded-full px-5 focus:ring-2 focus:ring-emerald-500 outline-none border-slate-300" placeholder="Ask about any trek...">
                <button onclick="ask()" id="sendBtn" class="bg-emerald-600 text-white px-8 py-3 rounded-full hover:bg-emerald-700 transition font-bold shadow-md">Send</button>
            </div>
        </div>
        <script>
            async function ask() {
                const input = document.getElementById('userInput');
                const box = document.getElementById('box');
                const btn = document.getElementById('sendBtn');
                const q = input.value.trim();
                if(!q) return;

                box.innerHTML += `<div class="bg-emerald-500 text-white msg self-end shadow-md">${q}</div>`;
                input.value = '';
                btn.disabled = true;
                box.scrollTop = box.scrollHeight;

                try {
                    const res = await fetch('/chat', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ question: q })
                    });
                    
                    const data = await res.json();
                    
                    if (data.answer) {
                        box.innerHTML += `<div class="bg-white text-slate-800 msg self-start border border-slate-200 shadow-sm"><b>TrekMate:</b><br>${data.answer}</div>`;
                    } else {
                        box.innerHTML += `<div class="bg-red-50 text-red-600 msg self-start border border-red-100 shadow-sm text-xs"><b>Error:</b><br>${data.error || 'Kuch galat hua'}</div>`;
                    }
                } catch (e) {
                    box.innerHTML += `<div class="bg-red-50 text-red-600 msg self-start border border-red-100 shadow-sm text-xs">Connection failed!</div>`;
                }
                btn.disabled = false;
                box.scrollTop = box.scrollHeight;
            }
            document.getElementById('userInput').addEventListener('keypress', (e) => { if(e.key==='Enter') ask(); });
        </script>
    </body>
    </html>
    """


@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        print(f"User Question: {request.question}")

        # ✅ Naya SDK: system instruction config ke through
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=request.question,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )

        # ✅ Response safely nikaalo
        text = None
        try:
            text = response.text
        except Exception:
            # Fallback: candidates se nikaalo
            if response.candidates and len(response.candidates) > 0:
                parts = response.candidates[0].content.parts
                text = "".join(p.text for p in parts if hasattr(p, "text"))

        if not text:
            return {"error": "AI ne khali response bheja. API key / quota check karein."}

        return {"answer": text}

    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"error": str(e)}


@app.get("/health")
async def health():
    """Quick test endpoint — browser mein kholo, chalega to API key sahi hai."""
    try:
        r = client.models.generate_content(
            model=MODEL_NAME,
            contents="Say 'OK' only.",
        )
        return {"status": "ok", "model": MODEL_NAME, "reply": r.text}
    except Exception as e:
        return {"status": "error", "message": str(e)}
