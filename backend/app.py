import os
import re
import random
import string
import time
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app, origins=["https://toolifyx.netlify.app", "http://localhost:7700"])

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.instagram.com/",
})

cache = {}

def random_string(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def clean_filename(text):
    if not text:
        text = "Instagram Video"
    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r'\s+', " ", text).strip()
    return text[:120]

def extract_shortcode(url):
    patterns = [
        r'instagram\.com/p/([^/?#&]+)',
        r'instagram\.com/reel/([^/?#&]+)',
        r'instagram\.com/reels/([^/?#&]+)',
        r'instagram\.com/tv/([^/?#&]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

# ==========================
# NEW: CLIENT-SIDE EXTRACTION ENDPOINT
# Accepts the direct video URL found by frontend
# ==========================

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "Instagram Video Proxy API",
        "version": "4.0",
        "methods": ["client-extract", "proxy-download"]
    })

@app.route("/api/health")
def health():
    return jsonify({
        "status": "healthy",
        "cache_size": len(cache)
    })

@app.route("/api/extract", methods=["POST"])
def extract():
    """
    NEW: Frontend sends the Instagram page HTML here,
    we extract video URLs server-side as a fallback.
    Or frontend can extract in browser and skip this.
    """
    data = request.get_json() or {}
    html = data.get("html", "")
    url = data.get("url", "").strip()

    if not html and not url:
        return jsonify({"success": False, "message": "No HTML or URL provided"}), 400

    # Try to extract from provided HTML first
    if html:
        # Look for video_url in Instagram's embedded JSON
        json_match = re.search(r'<script type="text/javascript">window\._sharedData = ({.*?});</script>', html, re.DOTALL)
        if json_match:
            try:
                import json
                shared_data = json.loads(json_match.group(1))
                media = shared_data.get("entry_data", {}).get("PostPage", [{}])[0].get("graphql", {}).get("shortcode_media", {})
                video_url = media.get("video_url")
                if video_url:
                    return jsonify({
                        "success": True,
                        "videoUrl": video_url,
                        "title": media.get("title", "Instagram Video"),
                        "thumbnail": media.get("display_url", ""),
                        "uploader": media.get("owner", {}).get("username", ""),
                        "is_video": media.get("is_video", False)
                    })
            except Exception as e:
                print(f"[extract] JSON parse error: {e}")

        # Fallback: regex for video URL in HTML
        vid_match = re.search(r'"video_url":"(https://[^"]+)"', html)
        if vid_match:
            return jsonify({
                "success": True,
                "videoUrl": vid_match.group(1).replace("\\u0026", "&"),
                "title": "Instagram Video",
                "thumbnail": "",
                "uploader": ""
            })

    # If no HTML provided, try to fetch the page ourselves (likely to fail on Render)
    if url:
        try:
            r = session.get(url, timeout=10, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            if r.status_code == 200:
                # Same extraction logic on fetched HTML
                json_match = re.search(r'<script type="text/javascript">window\._sharedData = ({.*?});</script>', r.text, re.DOTALL)
                if json_match:
                    try:
                        import json
                        shared_data = json.loads(json_match.group(1))
                        media = shared_data.get("entry_data", {}).get("PostPage", [{}])[0].get("graphql", {}).get("shortcode_media", {})
                        video_url = media.get("video_url")
                        if video_url:
                            return jsonify({
                                "success": True,
                                "videoUrl": video_url,
                                "title": media.get("title", "Instagram Video"),
                                "thumbnail": media.get("display_url", ""),
                                "uploader": media.get("owner", {}).get("username", ""),
                                "is_video": media.get("is_video", False)
                            })
                    except:
                        pass
        except Exception as e:
            print(f"[extract] Fetch error: {e}")

    return jsonify({
        "success": False,
        "message": "Could not extract video URL. Instagram may require login."
    }), 400


@app.route("/api/fetch", methods=["POST"])
def fetch():
    """
    LEGACY: Try multiple methods. Keep as fallback.
    """
    data = request.get_json() or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "message": "No URL provided"}), 400

    if "instagram.com" not in url:
        return jsonify({"success": False, "message": "Invalid Instagram URL"}), 400

    shortcode = extract_shortcode(url)
    if not shortcode:
        return jsonify({"success": False, "message": "Could not extract post ID from URL"}), 400

    # Try yt-dlp with cookies if available
    result = try_ytdlp(url)
    if result:
        return jsonify({
            "success": True,
            "videoUrl": result["video_url"],
            "title": result.get("title", "Instagram Video"),
            "thumbnail": result.get("thumbnail", ""),
            "uploader": result.get("uploader", ""),
            "source": "ytdlp"
        })

    return jsonify({
        "success": False,
        "message": "Failed to fetch video. Instagram requires authentication for most content now. Try using a browser extension or the client-side method.",
    }), 500


def try_ytdlp(url):
    """Try yt-dlp with cookies file if available"""
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "format": "best[ext=mp4]/best",
            "noplaylist": True,
            "socket_timeout": 10,
            "retries": 1,
            "nocheckcertificate": True,
            "no_warnings": True,
            "cookiesfrombrowser": None,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.instagram.com/",
            },
        }

        # Check for cookies file
        cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = cookies_path
            print(f"[ytdlp] Using cookies file: {cookies_path}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return None

        video_url = info.get("url")
        if not video_url and "formats" in info:
            formats = info.get("formats", [])
            for f in reversed(formats):
                if f.get("ext") == "mp4" and f.get("url"):
                    video_url = f["url"]
                    break
            if not video_url:
                for f in reversed(formats):
                    if f.get("url"):
                        video_url = f["url"]
                        break

        if video_url:
            return {
                "video_url": video_url,
                "title": info.get("title", "Instagram Video"),
                "thumbnail": info.get("thumbnail", ""),
                "uploader": info.get("uploader", ""),
            }

    except Exception as e:
        print(f"[ytdlp] Error: {e}")
    return None


@app.route("/api/download")
def download():
    """Proxy download to bypass CORS"""
    video_url = request.args.get("url")
    mode = request.args.get("mode", "download")
    custom_filename = request.args.get("filename", "")

    if not video_url:
        return jsonify({"success": False, "message": "No video URL"}), 400

    try:
        range_header = request.headers.get("Range")

        source_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "video/webm,video/mp4,video/*,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.instagram.com/",
        }
        if range_header:
            source_headers["Range"] = range_header

        r = session.get(video_url, stream=True, timeout=30, headers=source_headers, allow_redirects=True)

        if r.status_code in (301, 302, 307, 308):
            r = session.get(r.headers.get("Location", video_url), stream=True, timeout=30, headers=source_headers)

        if custom_filename:
            filename = clean_filename(custom_filename)
            if not filename.endswith(".mp4"):
                filename += ".mp4"
        else:
            rand = random_string()
            filename = f"IG-{rand}.mp4"

        status_code = 206 if (r.status_code == 206 or range_header) else 200

        headers = {
            "Content-Type": r.headers.get("Content-Type", "video/mp4"),
            "Accept-Ranges": "bytes",
        }

        if "Content-Range" in r.headers:
            headers["Content-Range"] = r.headers["Content-Range"]
        if "Content-Length" in r.headers:
            headers["Content-Length"] = r.headers["Content-Length"]

        if mode == "preview":
            headers["Content-Disposition"] = f'inline; filename="{filename}"'
        else:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'

        def generate():
            for chunk in r.iter_content(chunk_size=262144):
                if chunk:
                    yield chunk

        return Response(generate(), status=status_code, headers=headers)

    except Exception as e:
        print(f"[download] Error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
