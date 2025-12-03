import os
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    raise ValueError("❌ لم يتم العثور على OPENAI_API_KEY في متغيرات البيئة")

client = OpenAI(api_key=OPENAI_API_KEY)

def ask_gpt(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "أنت مساعد ذكي مختصر وتجيب بالعربية."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=800,
    )
    return response.choices[0].message.content.strip()
