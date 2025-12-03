import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from yt_dlp import YoutubeDL

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt-api")

# إعداد yt-dlp (بدون تحميل، فقط معلومات)
YDL_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "geo_bypass": True,
    # "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}


@app.get("/")
def root():
    return {"status": "ok", "service": "yt-helper-api"}


@app.get("/info")
def get_info(url: str = Query(..., description="رابط يوتيوب")):
    """
    يرجّع معلومات الفيديو + الجودات المتاحة (مع صوت).
    """
    try:
        with YoutubeDL(YDL_BASE_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error("Error in /info: %s", e)
        raise HTTPException(status_code=400, detail="فشل الحصول على معلومات الفيديو من يوتيوب")

    if not info:
        raise HTTPException(status_code=400, detail="لم تُستخرج معلومات الفيديو (info = None)")

    formats_raw = info.get("formats", []) or []
    formats_clean = []

    for f in formats_raw:
        height = f.get("height")
        if not height:
            continue

        # نريد فورمات فيه صوت وصورة معاً
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        if not vcodec or vcodec == "none":
            continue
        if not acodec or acodec == "none":
            continue

        formats_clean.append(
            {
                "format_id": f.get("format_id"),
                "height": height,
                "ext": f.get("ext"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }
        )

    # نرتب من الأصغر للأكبر
    formats_clean.sort(key=lambda x: x["height"] or 0)

    return JSONResponse(
        {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "formats": formats_clean,
        }
    )


@app.get("/direct_url")
def get_direct_url(
    url: str = Query(..., description="رابط يوتيوب"),
    height: Optional[int] = Query(None, description="أقصى ارتفاع (جودة) مطلوبة، مثلاً 360"),
):
    """
    يرجّع رابط مباشر لملف فيديو يحتوي على صوت وصورة (progressive) حسب الجودة المطلوبة.
    """
    try:
        with YoutubeDL(YDL_BASE_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error("Error in /direct_url: %s", e)
        raise HTTPException(status_code=400, detail="فشل الحصول على معلومات الفيديو من يوتيوب")

    if not info:
        raise HTTPException(status_code=400, detail="لم تُستخرج معلومات الفيديو (info = None)")

    formats_raw = info.get("formats", []) or []

    candidates: List[dict] = []
    for f in formats_raw:
        h = f.get("height")
        if not h:
            continue

        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        if not vcodec or vcodec == "none":
            continue
        if not acodec or acodec == "none":
            continue

        if height is not None and h > height:
            continue

        candidates.append(f)

    if not candidates:
        raise HTTPException(status_code=400, detail="لم أجد فورمات مناسب بهذه الجودة")

    # نختار أعلى جودة متاحة ضمن الحد المطلوب
    best = sorted(candidates, key=lambda x: x.get("height", 0))[-1]

    direct_url = best.get("url")
    if not direct_url:
        raise HTTPException(status_code=400, detail="لم أستطع استخراج الرابط المباشر للفيديو")

    return JSONResponse(
        {
            "url": direct_url,
            "height": best.get("height"),
            "ext": best.get("ext") or "mp4",
        }
      )
