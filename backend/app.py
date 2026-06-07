import os
import requests
import random
import string
import re
import time
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.instagram.com/",
})

# ==========================
# CACHE (simple in-memory)
# ==========================

cache = {}

# ==========================
# UTIL
# ==========================

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
# METHOD 1: SNAPINSTA.APP (Most reliable for public posts)
# ==========================

def fetch_snapinsta(url):
    """Scrape snapinsta.app - works for public posts without cookies"""
    try:
        # Get the page first to grab any tokens
        init = session.get("https://snapinsta.app/", timeout=10)
        if init.status_code != 200:
            return None

        # Try to find token
        token_match = re.search(r'name="_token"\s+value="([^"]+)"', init.text)
        token = token_match.group(1) if token_match else ""

        # Submit URL
        res = session.post(
            "https://snapinsta.app/action.php",
            data={"url": url, "token": token, "action": "post"},
            timeout=20,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://snapinsta.app",
                "Referer": "https://snapinsta.app/",
            }
        )

        if res.status_code != 200:
            return None

        text = res.text

        # Method A: Look for direct download link
        dl_match = re.search(r'href="(https://[^"]+\.mp4[^"]*)"', text)
        if dl_match:
            return {
                "video_url": dl_match.group(1),
                "title": "Instagram Video",
                "thumbnail": "",
                "uploader": "",
                "source": "snapinsta"
            }

        # Method B: Look for video tag source
        vid_match = re.search(r'<video[^>]+src="(https://[^"]+)"', text)
        if vid_match:
            return {
                "video_url": vid_match.group(1),
                "title": "Instagram Video",
                "thumbnail": "",
                "uploader": "",
                "source": "snapinsta"
            }

        # Method C: Look for any data-url attributes
        data_match = re.search(r'data-url="(https://[^"]+\.mp4[^"]*)"', text)
        if data_match:
            return {
                "video_url": data_match.group(1),
                "title": "Instagram Video",
                "thumbnail": "",
                "uploader": "",
                "source": "snapinsta"
            }

    except Exception as e:
        print(f"[snapinsta] Error: {e}")
    return None


# ==========================
# METHOD 2: SAVEFROM.NET API
# ==========================

def fetch_savefrom(url):
    """Use savefrom.net API"""
    try:
        res = session.post(
            "https://savefrom.net/api/convert",
            data={"url": url},
            timeout=20,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://savefrom.net",
                "Referer": "https://savefrom.net/",
            }
        )
        if res.status_code == 200:
            try:
                data = res.json()
                video_url = data.get("url") or data.get("download_url")
                if video_url:
                    return {
                        "video_url": video_url,
                        "title": data.get("meta", {}).get("title", "Instagram Video"),
                        "thumbnail": data.get("thumb", ""),
                        "uploader": "",
                        "source": "savefrom"
                    }
            except:
                # Sometimes returns HTML instead of JSON
                pass
    except Exception as e:
        print(f"[savefrom] Error: {e}")
    return None


# ==========================
# METHOD 3: YT-DLP (Last resort - usually fails without cookies)
# ==========================

def fetch_ytdlp(url):
    """Try yt-dlp - will likely fail without cookies but worth a shot"""
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
                "source": "ytdlp"
            }

    except Exception as e:
        print(f"[ytdlp] Error: {e}")
    return None


# ==========================
# METHOD 4: RAPIDAPI (Requires API key)
# ==========================

def fetch_rapidapi(url):
    """RapidAPI Instagram downloader"""
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        return None

    try:
        res = session.get(
            "https://instagram-downloader-download-instagram-videos-stories1.p.rapidapi.com/get-info-rapid/",
            params={"url": url},
            timeout=15,
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": "instagram-downloader-download-instagram-videos-stories1.p.rapidapi.com",
            }
        )
        if res.status_code == 200:
            data = res.json()
            video_url = data.get("video_url") or data.get("download_url")
            if video_url:
                return {
                    "video_url": video_url,
                    "title": data.get("title", "Instagram Video"),
                    "thumbnail": data.get("thumbnail", ""),
                    "uploader": data.get("username", ""),
                    "source": "rapidapi"
                }
    except Exception as e:
        print(f"[rapidapi] Error: {e}")
    return None


# ==========================
# FETCH CONTROLLER
# ==========================

def get_video(url):
    """Try multiple methods, return first success"""
    if url in cache:
        # Cache expires after 10 minutes
        if time.time() - cache[url].get("cached_at", 0) < 600:
            return cache[url]["data"]
        else:
            del cache[url]

    methods = [
        ("snapinsta", fetch_snapinsta),
        ("savefrom", fetch_savefrom),
        ("rapidapi", fetch_rapidapi),
        ("ytdlp", fetch_ytdlp),
    ]

    errors = []

    for name, method in methods:
        try:
            print(f"[fetch] Trying {name}...")
            result = method(url)
            if result and result.get("video_url"):
                print(f"[fetch] Success with {name}")
                cache[url] = {
                    "data": result,
                    "cached_at": time.time()
                }
                return result
        except Exception as e:
            err_msg = f"{name}: {str(e)}"
            errors.append(err_msg)
            print(f"[fetch] {err_msg}")

    return {"error": "All methods failed", "details": errors}


# ==========================
# API ROUTES
# ==========================

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "Instagram Public Video API",
        "version": "3.0",
        "methods": ["snapinsta", "savefrom", "rapidapi", "ytdlp"]
    })


@app.route("/api/health")
def health():
    """Debug endpoint to check if service is running"""
    return jsonify({
        "status": "healthy",
        "cache_size": len(cache),
        "rapidapi_configured": bool(os.getenv("RAPIDAPI_KEY"))
    })


@app.route("/api/fetch", methods=["POST"])
def fetch():
    data = request.get_json() or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "message": "No URL provided"}), 400

    if "instagram.com" not in url:
        return jsonify({"success": False, "message": "Invalid Instagram URL"}), 400

    shortcode = extract_shortcode(url)
    if not shortcode:
        return jsonify({"success": False, "message": "Could not extract post ID from URL"}), 400

    result = get_video(url)

    if "error" in result:
        return jsonify({
            "success": False,
            "message": "Failed to fetch video. Instagram may require login, or the post is private/deleted.",
            "debug": result.get("details", [])
        }), 500

    return jsonify({
        "success": True,
        "videoUrl": result["video_url"],
        "title": result.get("title", "Instagram Video"),
        "thumbnail": result.get("thumbnail", ""),
        "uploader": result.get("uploader", ""),
        "source": result.get("source", "unknown")
    })


@app.route("/api/download")
def download():
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

        # Handle case where URL redirects
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


# ==========================
# START
# ==========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
