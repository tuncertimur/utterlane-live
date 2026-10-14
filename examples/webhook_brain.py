"""Minimal webhook brain for Utterlane Live.

Run:   python examples/webhook_brain.py           (needs: pip install fastapi uvicorn)
Then:  UTTERLANE_BRAIN=webhook  UTTERLANE_BRAIN_URL=http://127.0.0.1:9000/utterlane

Replace `answer()` with a call to your own agent, rules engine or LLM.
"""

import uvicorn
from fastapi import FastAPI

app = FastAPI()


def answer(latest: str, language: str) -> str:
    # latest looks like "[Speaker: Tuncer] what time is it?" (or "[Konuşan: …]" in Turkish)
    speaker, _, text = latest.partition("] ")
    speaker = speaker.split(":", 1)[-1].strip()
    if "?" not in text:
        return ""  # empty reply = stay silent
    if language == "tr":
        return f"{speaker}, bunu webhook örneğinden yanıtlıyorum."
    return f"{speaker}, this answer comes from the example webhook."


@app.post("/utterlane")
async def utterlane(payload: dict) -> dict:
    return {"reply": answer(payload.get("latest", ""), payload.get("language", "en"))}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
