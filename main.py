# pip install fastapi uvicorn requests google-genai python-dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import requests
from google import genai
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

# CORS allow karna taaki Frontend connect ho sake
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

@app.get("/")
def home():
    return {"message": "TrekMate API is running"}

@app.post("/chat")
async def chat(data: dict):
    question = data.get("question")
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=f"You are TrekMate. Answer this trekking query: {question}"
        )
        return {"answer": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Note: Aap baki functions (weather, wiki) ko bhi isi tarah endpoints mein badal sakte hain.
