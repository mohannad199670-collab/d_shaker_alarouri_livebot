import os
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

def analyze_youtube_link(video_url: str):
    """
    يرسل رابط يوتيوب إلى نموذج OpenAI لتحليل:
    - مدة الفيديو
    - الجودات المتاحة
    ويرجع dict جاهز للاستخدام داخل البوت.
    """

    prompt = f"""
    Analyze this YouTube video URL: {video_url}
    Return a JSON object with:
    - duration_seconds (integer)
    - available_qualities (list of integers like [144,240,360,480,720,1080])
    - title
    If the video is unavailable, return: {{"error": "unavailable"}}
    """

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    payload = {
        "model": "gpt-4.1-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    }

    response = requests.post(OPENAI_URL, headers=headers, json=payload)

    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
        return eval(content)  # لأن الرد JSON نصي
    except Exception as e:
        return {"error": str(e)}
