from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from google import genai
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key - Render ke Environment Variables mein add karein
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

class ChatRequest(BaseModel):
    question: str

# 1. UI dikhane ke liye endpoint (Frontend)
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
            .msg { max-width: 80%; margin-bottom: 10px; padding: 10px; border-radius: 10px; }
        </style>
    </head>
    <body class="bg-slate-100 flex flex-col items-center p-4">
        <div class="w-full max-w-2xl bg-white shadow-2xl rounded-xl overflow-hidden mt-5">
            <div class="bg-emerald-600 p-4 text-white text-xl font-bold text-center">🥾 TrekMate Assistant</div>
            <div id="box" class="chat-window p-4 overflow-y-auto flex flex-col bg-slate-50 border-b">
                <div class="bg-slate-200 text-slate-800 msg self-start">Namaste! Main TrekMate AI hoon. Main aapki kya madad kar sakta hoon?</div>
            </div>
            <div class="p-4 flex gap-2">
                <input type="text" id="userInput" class="flex-1 p-2 border rounded-full px-4 focus:ring-2 focus:ring-emerald-500 outline-none" placeholder="Ask about any trek...">
                <button onclick="ask()" class="bg-emerald-600 text-white px-6 py-2 rounded-full hover:bg-emerald-700 transition font-bold">Send</button>
            </div>
        </div>
        <script>
            async function ask() {
                const input = document.getElementById('userInput');
                const box = document.getElementById('box');
                const q = input.value.trim();
                if(!q) return;

                box.innerHTML += `<div class="bg-emerald-500 text-white msg self-end shadow-sm">${q}</div>`;
                input.value = '';
                box.scrollTop = box.scrollHeight;

                try {
                    const res = await fetch('/chat', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ question: q })
                    });
                    const data = await res.json();
                    box.innerHTML += `<div class="bg-white text-slate-800 msg self-start border shadow-sm"><b>TrekMate:</b><br>${data.answer}</div>`;
                } catch (e) {
                    box.innerHTML += `<div class="text-red-500 text-center text-xs">Connection Error!</div>`;
                }
                box.scrollTop = box.scrollHeight;
            }
            document.getElementById('userInput').addEventListener('keypress', (e) => { if(e.key==='Enter') ask(); });
        </script>
    </body>
    </html>
    """

# 2. AI Logic ke liye endpoint (Backend)
@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"You are TrekMate, a helpful trekking assistant. Answer: {request.question}"
        )
        return {"answer": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
