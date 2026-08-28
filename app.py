"""
Hidden Haven Threads (HHT) — Vision + Listing Automation API
============================================================
Single-file Flask pipeline. Upload a garment photo to POST /analyze and get back a
full listing package:

    {
      "demo": true/false,
      "vision":  { "labels": [...], "colors": ["#hex", ...], "text": "..." },
      "sku":     { "title", "category", "condition_id", "description", "code", "barcode" },
      "pricing": { "list_price", "floor", "auction_start", "accept_offer", "decline_offer",
                   "ebay", "depop", "poshmark", "etsy", "mercari" },
      "seo":     { "title", "meta_description", "keywords": [...], "platform_routing": [...] }
    }

Runs with NO API keys (demo mode extracts real colors from the image and uses a
curated vintage catalog). Add GEMINI_API_KEY for real per-image AI vision analysis.
Add DO_SPACES_* / APPWRITE_* keys to persist uploads + inventory.
"""

import os
import io
import re
import json
import math
import uuid
import hashlib
import mimetypes
from collections import Counter

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ---------- optional dependencies (graceful degradation) ----------
try:
    import requests
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import boto3
    HAS_BOTO3 = True
except Exception:
    HAS_BOTO3 = False

try:
    from appwrite.client import Client
    from appwrite.services.databases import Databases
    HAS_APPWRITE = True
except Exception:
    HAS_APPWRITE = False

import base64


# ---------- configuration ----------
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Azure AI Foundry / Azure OpenAI vision fallback (optional)
AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")  # e.g. https://hht.openai.azure.com
AZURE_KEY = os.environ.get("AZURE_OPENAI_KEY")
AZURE_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

DO_SPACES_KEY = os.environ.get("DO_SPACES_KEY")
DO_SPACES_SECRET = os.environ.get("DO_SPACES_SECRET")
DO_SPACES_REGION = os.environ.get("DO_SPACES_REGION", "nyc3")
DO_SPACES_BUCKET = os.environ.get("DO_SPACES_BUCKET")
DO_SPACES_ENDPOINT = f"https://{DO_SPACES_REGION}.digitaloceanspaces.com"

APPWRITE_ENDPOINT = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
APPWRITE_PROJECT = os.environ.get("APPWRITE_PROJECT_ID")
APPWRITE_KEY = os.environ.get("APPWRITE_API_KEY")
APPWRITE_DB = os.environ.get("APPWRITE_DATABASE_ID", "default")
APPWRITE_COLL = os.environ.get("APPWRITE_COLLECTION_ID", "inventory")

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
PORT = int(os.environ.get("PORT", 8080))
LOCAL_UPLOAD_DIR = os.environ.get("LOCAL_UPLOAD_DIR", "/data/uploads")
SAVE_UPLOADS = os.environ.get("SAVE_UPLOADS", "true").lower() in {"1", "true", "yes", "on"}
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

app = Flask(__name__, static_folder="frontend/dist", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(app, resources={r"/*": {"origins": CORS_ORIGINS}})


# =====================================================================
# CATALOG — vintage apparel pricing & taxonomy reference data
# =====================================================================
BASE_PRICE = {
    "jacket": 45, "jeans": 30, "dress": 35, "shirt": 20, "tee": 15,
    "sweater": 30, "skirt": 25, "boots": 40, "sneakers": 35, "bag": 30,
    "hat": 12, "accessory": 10, "vest": 22, "coat": 50, "shorts": 18,
}

BRAND_MULTIPLIER = {
    "levi's": 1.8, "levis": 1.8, "carhartt": 2.2, "nike": 1.6, "adidas": 1.5,
    "ralph lauren": 2.4, "tommy hilfiger": 1.7, "calvin klein": 1.6,
    "burberry": 3.5, "gucci": 5.0, "prada": 4.5, "vintage": 1.0, "unbranded": 1.0,
    "the north face": 2.0, "patagonia": 1.9, "champion": 1.4, "wrangler": 1.3,
    "lee": 1.3, "dickies": 1.5, "stussy": 2.0, "supreme": 3.0,
    "harley davidson": 1.8, "pendleton": 2.2, "lacoste": 1.8, "fila": 1.3,
    "umbro": 1.3, "kappa": 1.3, "oakley": 1.6, "fila": 1.3, "guess": 1.5,
    "armani": 3.0, "versace": 4.0, "diesel": 1.8, "true religion": 2.0,
    "new balance": 1.5, "asics": 1.5, "salomon": 1.7, "doc martens": 1.8,
    "dr. martens": 1.8,
}

ERA_PREMIUM = {
    "50s": 1.8, "60s": 1.7, "70s": 1.6, "80s": 1.3, "90s": 1.5,
    "y2k": 1.4, "00s": 1.4, "2000s": 1.4, "modern": 1.0, "vintage": 1.3,
}

# 2-char SKU era codes (explicit map — NOT era[:2], which breaks for "90s" etc.)
ERA_CODE = {
    "50s": "50", "60s": "60", "70s": "70", "80s": "80", "90s": "90",
    "y2k": "Y2", "00s": "00", "2000s": "00", "modern": "MD", "vintage": "VT",
}

CONDITION_FACTOR = {
    "new": 1.0, "new with tags": 1.0, "nwt": 1.0,
    "like new": 0.9, "excellent": 0.9, "like-new": 0.9,
    "very good": 0.8, "very-good": 0.8, "good": 0.65, "fair": 0.45,
}

# Nearest-name color lookup for SKU color code + label
COLOR_MAP = [
    ("indigo", "IND", "indigo"), ("denim", "IND", "indigo"), ("navy", "NVY", "navy"),
    ("blue", "BLU", "blue"), ("black", "BLK", "black"), ("white", "WHT", "white"),
    ("red", "RED", "red"), ("maroon", "MRN", "maroon"), ("burgundy", "BRG", "burgundy"),
    ("green", "GRN", "green"), ("olive", "OLV", "olive"), ("brown", "BRN", "brown"),
    ("tan", "TAN", "tan"), ("beige", "BGE", "beige"), ("cream", "CRM", "cream"),
    ("grey", "GRY", "grey"), ("gray", "GRY", "grey"), ("pink", "PNK", "pink"),
    ("yellow", "YLW", "yellow"), ("gold", "GLD", "gold"), ("orange", "ORG", "orange"),
    ("purple", "PRP", "purple"), ("teal", "TEL", "teal"), ("rust", "RST", "rust"),
    ("khaki", "KHK", "khaki"), ("charcoal", "CHR", "charcoal"),
]

PLATFORM_TAKE = {  # platform fee fraction
    "ebay": 0.13, "depop": 0.066, "poshmark": 0.20, "etsy": 0.065, "mercari": 0.10,
}

PLATFORM_ROUTING = {
    "90s": ["depop", "ebay", "mercari"], "y2k": ["depop", "mercari", "ebay"],
    "80s": ["etsy", "ebay", "depop"], "70s": ["etsy", "ebay"], "60s": ["etsy", "ebay"],
    "vintage": ["etsy", "ebay", "poshmark"], "modern": ["ebay", "poshmark", "mercari"],
}


# =====================================================================
# VISION — provider-agnostic (Gemini primary, demo fallback)
# =====================================================================
def _extract_colors(image_bytes, n=5):
    """Pull dominant colors out of an image with PIL (no AI key needed)."""
    if not HAS_PIL:
        return []
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((120, 120))
        q = img.quantize(colors=n, method=Image.MEDIANCUT).convert("RGB")
        pixels = list(q.getdata())
        counts = Counter(pixels)
        out = []
        for rgb, _ in counts.most_common(n):
            out.append("#%02x%02x%02x" % rgb)
        return out
    except Exception:
        return []


def _nearest_color(hex_color):
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    best, best_d = None, 1e9
    for name, code, label in COLOR_MAP:
        # reference rgb for each named color
        ref = _NAMED_RGB.get(name, (128, 128, 128))
        d = (r - ref[0]) ** 2 + (g - ref[1]) ** 2 + (b - ref[2]) ** 2
        if d < best_d:
            best, best_d = (name, code, label), d
    return best


# rough reference RGBs for color matching
_NAMED_RGB = {
    "indigo": (75, 0, 130), "denim": (45, 84, 130), "navy": (0, 0, 128),
    "blue": (0, 0, 255), "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (220, 20, 20), "maroon": (128, 0, 0), "burgundy": (128, 0, 32),
    "green": (0, 128, 0), "olive": (128, 128, 0), "brown": (139, 69, 19),
    "tan": (210, 180, 140), "beige": (245, 245, 220), "cream": (255, 248, 220),
    "grey": (128, 128, 128), "pink": (255, 192, 203), "yellow": (255, 255, 0),
    "gold": (255, 215, 0), "orange": (255, 165, 0), "purple": (128, 0, 128),
    "teal": (0, 128, 128), "rust": (183, 65, 14), "khaki": (195, 176, 145),
    "charcoal": (54, 69, 79),
}

# Filename hints for demo-mode garment guessing
_FILENAME_HINTS = [
    ("jacket", "jacket"), ("denim", "jacket"), ("jean", "jeans"), ("pants", "jeans"),
    ("dress", "dress"), ("shirt", "shirt"), ("tee", "tee"), ("tshirt", "tee"),
    ("t-shirt", "tee"), ("sweater", "sweater"), ("knit", "sweater"), ("skirt", "skirt"),
    ("boot", "boots"), ("sneaker", "sneakers"), ("shoe", "sneakers"), ("bag", "bag"),
    ("purse", "bag"), ("hat", "hat"), ("cap", "hat"), ("vest", "vest"), ("coat", "coat"),
    ("short", "shorts"),
]

DEMO_SAMPLE = {
    "brand": "Levi's", "garment_type": "jacket", "era": "90s", "color": "indigo",
    "size": "L", "condition": "very good", "designer_tier": "mid",
    "title": "Levi's Vintage 90s Denim Trucker Jacket",
    "description": "Classic Levi's Type III denim trucker jacket, 90s production. "
                   "Indigo wash with natural fade, brass buttons, two chest pockets, "
                   "cotton denim. Excellent vintage condition with light wear.",
    "labels": ["denim jacket", "trucker", "vintage", "90s", "cotton", "brass buttons"],
    "text": "LEVI'S  Made in USA  Size L",
}


def _guess_type_from_filename(name):
    n = name.lower()
    for keyword, gtype in _FILENAME_HINTS:
        if keyword in n:
            return gtype
    return None


def analyze_with_gemini(image_bytes, mime_type):
    """Real AI vision via Gemini REST API when GEMINI_API_KEY is configured."""
    if not (HAS_REQUESTS and GEMINI_KEY):
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"x-goog-api-key": GEMINI_KEY}
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You are a vintage apparel expert. Analyze this garment photo and return "
        "ONLY raw JSON (no markdown) with keys: brand, garment_type, era, color, "
        "size, condition, designer_tier (low|mid|high), title, description, "
        "labels (array of short tags), text (any visible text/logos). "
        "Use era codes like 90s, 80s, y2k, 70s, 60s, vintage, modern."
    )
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": b64}},
        ]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
        return json.loads(text)
    except Exception as e:
        print(f"[vision] gemini failed: {e}")
        return None


def analyze_with_azure(image_bytes, mime_type):
    """Azure AI Foundry (Azure OpenAI GPT-4o vision) fallback. Returns dict or None."""
    if not (AZURE_ENDPOINT and AZURE_KEY and AZURE_DEPLOYMENT):
        return None
    try:
        import urllib.request, base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type or 'image/jpeg'};base64,{b64}"
        url = (
            f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_DEPLOYMENT}"
            f"/chat/completions?api-version={AZURE_API_VERSION}"
        )
        body = json.dumps({
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "You are a vintage apparel expert. Analyze this garment photo and "
                        "return ONLY raw JSON (no markdown) with keys: brand, garment_type, "
                        "era, color, size, condition, designer_tier (low|mid|high), title, "
                        "description, labels (array of short tags), text (visible text/logos). "
                        "Use era codes like 90s, 80s, y2k, 70s, 60s, vintage, modern.")},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            "max_tokens": 800,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "api-key": AZURE_KEY},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
        return json.loads(text)
    except Exception as e:
        print(f"[vision] azure failed: {e}")
        return None


def run_vision(image_bytes, mime_type, filename):
    """Provider-agnostic vision router. Gemini -> Azure -> demo fallback."""
    ai = analyze_with_gemini(image_bytes, mime_type)
    provider = "gemini" if ai else None
    if not ai:
        ai = analyze_with_azure(image_bytes, mime_type)
        if ai:
            provider = "azure"
    colors = _extract_colors(image_bytes)
    if ai:
        primary = provider
        data = ai
    else:
        primary = "demo"
        data = dict(DEMO_SAMPLE)
        gtype = _guess_type_from_filename(filename or "")
        if gtype:
            data["garment_type"] = gtype
            data["title"] = f"Vintage {gtype.capitalize()}"
            data["category"] = gtype
    # attach real extracted colors + nearest color code
    nearest = _nearest_color(colors[0]) if colors else ("indigo", "IND", "indigo")
    data["color_code"] = nearest[1]
    data["color_label"] = nearest[2]
    data["vision_colors"] = colors
    data["provider"] = primary
    data["fallback_triggered"] = primary == "demo"
    return data


# =====================================================================
# SKU GENERATOR  —  HHT-TYPE3-ERA2-COLOR3-SIZE3-SEQ4
# =====================================================================
TYPE_CODE = {
    "jacket": "JKT", "jeans": "JNS", "dress": "DRS", "shirt": "SHT", "tee": "TEE",
    "sweater": "SWT", "skirt": "SKR", "boots": "BTS", "sneakers": "SNK", "bag": "BAG",
    "hat": "HAT", "accessory": "ACC", "vest": "VST", "coat": "COT", "shorts": "SHT",
}

SIZE_CODE = {
    "xs": "XSM", "s": "SML", "m": "MED", "l": "LRG", "xl": "XLG", "xxl": "XXL",
    "one size": "OSZ", "os": "OSZ", "2xl": "XXL", "3xl": "3XL",
}


def _seq_from_hash(*parts):
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return h[:4].upper()


def generate_sku(data):
    gtype = (data.get("garment_type") or "accessory").lower()
    type_code = TYPE_CODE.get(gtype, "ACC")
    era = (data.get("era") or "vintage").lower()
    era_code = ERA_CODE.get(era, "VT")
    color_code = data.get("color_code", "GEN")
    size = (data.get("size") or "OS").lower()
    size_code = SIZE_CODE.get(size, size[:3].upper() or "OSZ")
    seq = _seq_from_hash(type_code, era_code, color_code, size_code, data.get("title", ""))
    code = f"HHT-{type_code}-{era_code}-{color_code}-{size_code}-{seq}"
    # EAN-13 compatible 12-digit numeric (from hex)
    barcode = str(int(hashlib.md5(code.encode()).hexdigest()[:10], 16))[:12].zfill(12)
    return {
        "title": data.get("title", "Vintage garment"),
        "category": gtype,
        "condition_id": _condition_id(data.get("condition", "good")),
        "description": data.get("description", ""),
        "code": code,
        "barcode": barcode,
    }


def _condition_id(condition):
    c = (condition or "good").lower()
    return {
        "new": "new", "nwt": "new", "like new": "likenew", "excellent": "excellent",
        "very good": "verygood", "good": "good", "fair": "fair",
    }.get(c, "good")


# =====================================================================
# PRICING ENGINE — 8-factor scoring model
# =====================================================================
def compute_pricing(data):
    gtype = (data.get("garment_type") or "accessory").lower()
    base = BASE_PRICE.get(gtype, 15)

    brand = (data.get("brand") or "unbranded").lower()
    brand_mult = BRAND_MULTIPLIER.get(brand, 1.0)

    era = (data.get("era") or "vintage").lower()
    era_premium = ERA_PREMIUM.get(era, 1.3)

    condition = (data.get("condition") or "good").lower()
    cond_factor = CONDITION_FACTOR.get(condition, 0.65)

    tier = (data.get("designer_tier") or "low").lower()
    tier_add = {"high": 75, "mid": 15, "low": 0}.get(tier, 0)

    trend = 1.0  # static trend baseline; hook for future trend API
    special = 0  # hook for special features (rare prints, collabs)

    raw = base * brand_mult * era_premium * cond_factor * trend + tier_add + special
    if raw >= 10:
        list_price = round(math.floor(raw) - 0.01, 2)  # .99 retail ending
    else:
        list_price = round(raw, 2)

    floor = round(list_price * 0.55, 2)
    auction_start = round(list_price * 0.70, 2)
    accept_offer = round(list_price * 0.85, 2)
    decline_offer = round(list_price * 0.95, 2)

    platforms = {}
    for plat, fee in PLATFORM_TAKE.items():
        platforms[plat] = round(list_price / (1 - fee), 2)

    return {
        "list_price": list_price,
        "floor": floor,
        "auction_start": auction_start,
        "accept_offer": accept_offer,
        "decline_offer": decline_offer,
        "ebay": platforms["ebay"],
        "depop": platforms["depop"],
        "poshmark": platforms["poshmark"],
        "etsy": platforms["etsy"],
        "mercari": platforms["mercari"],
    }


# =====================================================================
# SEO LISTING GENERATOR
# =====================================================================
AESTHETIC_TAGS = {
    "90s": ["y2k", "90sfashion", "vintage", "streetwear", "retro", "denim", "grunge"],
    "y2k": ["y2k", "y2kfashion", "2000s", "lowrise", "bling", "retro", "vintage"],
    "80s": ["80s", "vintage", "retro", "preppy", "bold", "neon", "streetwear"],
    "70s": ["70s", "vintage", "boho", "retro", "cottagecore", "flared", "groovy"],
    "vintage": ["vintage", "retro", "thrifted", "sustainable", "vintagestyle", "classic"],
    "modern": ["streetwear", "minimal", "contemporary", "fashion", "trendy"],
}


def build_seo(data):
    title = (data.get("title") or "Vintage garment").strip()
    if len(title) > 80:
        title = title[:77].rstrip() + "..."
    era = (data.get("era") or "vintage").lower()
    brand = data.get("brand") or "Vintage"
    color = data.get("color_label") or "vintage"
    gtype = data.get("garment_type") or "garment"
    condition = data.get("condition") or "good"

    meta = (
        f"{brand} {era} {color} {gtype} in {condition} condition. "
        f"Authentic vintage, ready to ship from Hidden Haven Threads."
    )[:155]

    keywords = []
    keywords.append(brand.lower())
    keywords.append(f"{era} {gtype}")
    keywords.append(f"{color} {gtype}")
    keywords.append(f"{era} {color}")
    keywords.append("vintage clothing")
    keywords.append("hidden haven threads")
    keywords += AESTHETIC_TAGS.get(era, AESTHETIC_TAGS["vintage"])
    # dedupe preserving order
    seen = set()
    keywords = [k for k in keywords if not (k in seen or seen.add(k))][:15]

    routing = PLATFORM_ROUTING.get(era, PLATFORM_ROUTING["vintage"])

    return {
        "title": title,
        "meta_description": meta,
        "keywords": keywords,
        "platform_routing": routing,
    }


# =====================================================================
# STORAGE (optional) — DigitalOcean Spaces + Appwrite
# =====================================================================
def upload_to_spaces(file_bytes, content_type, ext):
    if not (HAS_BOTO3 and DO_SPACES_KEY and DO_SPACES_SECRET and DO_SPACES_BUCKET):
        return None
    try:
        session = boto3.session.Session()
        s3 = session.client(
            "s3", region_name=DO_SPACES_REGION, endpoint_url=DO_SPACES_ENDPOINT,
            aws_access_key_id=DO_SPACES_KEY, aws_secret_access_key=DO_SPACES_SECRET,
        )
        key = f"uploads/{uuid.uuid4()}{ext}"
        s3.put_object(
            Bucket=DO_SPACES_BUCKET, Key=key, Body=file_bytes,
            ContentType=content_type or "image/jpeg", ACL="public-read",
        )
        return f"https://{DO_SPACES_BUCKET}.{DO_SPACES_REGION}.digitaloceanspaces.com/{key}"
    except Exception as e:
        print(f"[storage] spaces upload failed: {e}")
        return None


def save_to_appwrite(doc):
    if not (HAS_APPWRITE and APPWRITE_PROJECT and APPWRITE_KEY):
        return None
    try:
        client = Client().set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT).set_key(APPWRITE_KEY)
        db = Databases(client)
        doc_id = str(uuid.uuid4())[:8]
        db.create_document(APPWRITE_DB, APPWRITE_COLL, doc_id, doc)
        return doc_id
    except Exception as e:
        print(f"[storage] appwrite save failed: {e}")
        return None


def save_upload_locally(file_bytes, filename):
    if not SAVE_UPLOADS:
        return None
    try:
        os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)
        _, ext = os.path.splitext(filename or "")
        ext = ext.lower() if ext else ".jpg"
        safe_name = f"{uuid.uuid4()}{ext}"
        path = os.path.join(LOCAL_UPLOAD_DIR, safe_name)
        with open(path, "wb") as out:
            out.write(file_bytes)
        return safe_name
    except Exception as e:
        print(f"[storage] local upload save failed: {e}")
        return None


def normalize_mime_type(upload):
    detected = upload.content_type
    if detected in ALLOWED_IMAGE_TYPES:
        return detected
    guessed, _ = mimetypes.guess_type(upload.filename or "")
    if guessed in ALLOWED_IMAGE_TYPES:
        return guessed
    return detected


# =====================================================================
# PIPELINE + ROUTES
# =====================================================================
def run_pipeline(image_bytes, mime_type, filename):
    vision_data = run_vision(image_bytes, mime_type, filename)
    sku = generate_sku(vision_data)
    pricing = compute_pricing(vision_data)
    seo = build_seo(vision_data)

    local_upload_id = save_upload_locally(image_bytes, filename)
    public_url = upload_to_spaces(image_bytes, mime_type, os.path.splitext(filename)[1])
    if public_url:
        sku["image_url"] = public_url
        doc = {
            "item_id": sku["code"], "title": sku["title"], "category": sku["category"],
            "condition": sku["condition_id"], "description": sku["description"],
            "price": pricing["list_price"], "image_urls": [public_url],
        }
        save_to_appwrite(doc)

    draft = {
        "title": sku["title"],
        "description": sku["description"],
        "condition": sku["condition_id"],
        "price_suggestion": pricing["list_price"],
        "tags": seo["keywords"],
        "sku": sku["code"],
    }

    return {
        "demo": vision_data.get("provider") == "demo",
        "provider": vision_data.get("provider"),
        "fallback_triggered": vision_data.get("fallback_triggered", False),
        "draft": draft,
        "storage": {
            "local_upload_id": local_upload_id,
            "public_image_url": public_url,
        },
        "vision": {
            "labels": vision_data.get("labels", []),
            "colors": vision_data.get("vision_colors", []),
            "text": vision_data.get("text", ""),
        },
        "sku": sku,
        "pricing": pricing,
        "seo": seo,
    }


@app.route("/analyze", methods=["POST"])
def analyze():
    f = request.files.get("file") or request.files.get("files")
    if not f:
        return jsonify({"error": "No image uploaded. Use multipart form field 'file'."}), 400
    mime_type = normalize_mime_type(f)
    if mime_type not in ALLOWED_IMAGE_TYPES:
        return jsonify({"error": f"Unsupported file type '{f.content_type}'. Upload JPEG, PNG, WebP, or GIF."}), 415
    image_bytes = f.read()
    if not image_bytes:
        return jsonify({"error": "Empty file"}), 400
    try:
        result = run_pipeline(image_bytes, mime_type, f.filename)
        return jsonify(result)
    except Exception as e:
        print(f"[analyze] error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/bulk-analyze", methods=["POST"])
def bulk_analyze():
    """Process many garment photos at once. Returns one listing per image.
    Form field: 'files' (repeatable) or 'file'."""
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400
    results = []
    for f in files:
        entry = {"filename": f.filename, "status": "error", "error": None}
        try:
            mime_type = normalize_mime_type(f)
            if mime_type not in ALLOWED_IMAGE_TYPES:
                raise ValueError(f"Unsupported file type '{f.content_type}'")
            data = f.read()
            if not data:
                raise ValueError("Empty file")
            entry.update(run_pipeline(data, mime_type, f.filename))
            entry["status"] = "ok"
            entry.pop("error", None)
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)
    demo = results[0].get("demo", True) if results else True
    return jsonify({"count": len(results), "demo": demo, "results": results})


@app.route("/export/csv", methods=["POST"])
def export_csv():
    """Convert a batch of pipeline results into a cross-listing CSV.
    Body: {results: [...]} (the /bulk-analyze output)."""
    import csv as _csv
    body = request.get_json(silent=True) or {}
    rows = body.get("results", [])
    cols = ["filename", "title", "sku_code", "barcode", "category", "condition_id",
            "list_price", "floor", "auction_start", "accept_offer", "decline_offer",
            "ebay", "depop", "poshmark", "etsy", "mercari", "platform_routing",
            "seo_title", "meta_description", "description", "demo"]
    out = io.StringIO()
    w = _csv.writer(out)
    w.writerow(cols)
    for r in rows:
        sku = r.get("sku", {}) or {}
        pr = r.get("pricing", {}) or {}
        seo = r.get("seo", {}) or {}
        w.writerow([
            r.get("filename"), sku.get("title"), sku.get("code"), sku.get("barcode"),
            sku.get("category"), sku.get("condition_id"), pr.get("list_price"),
            pr.get("floor"), pr.get("auction_start"), pr.get("accept_offer"),
            pr.get("decline_offer"), pr.get("ebay"), pr.get("depop"),
            pr.get("poshmark"), pr.get("etsy"), pr.get("mercari"),
            "/".join(seo.get("platform_routing", [])), seo.get("title"),
            seo.get("meta_description"), sku.get("description"), r.get("demo"),
        ])
    from flask import Response
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=hht_listings.csv"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "gemini": bool(GEMINI_KEY),
        "azure": bool(AZURE_ENDPOINT and AZURE_KEY),
        "spaces": bool(HAS_BOTO3 and DO_SPACES_KEY and DO_SPACES_BUCKET),
        "appwrite": bool(HAS_APPWRITE and APPWRITE_PROJECT),
        "local_upload_dir": LOCAL_UPLOAD_DIR,
        "save_uploads": SAVE_UPLOADS,
        "pil": HAS_PIL,
    })


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": f"Uploaded image is too large. Limit is {MAX_UPLOAD_MB} MB."}), 413


@app.errorhandler(404)
def not_found(_err):
    if request.path.startswith("/api/") or request.path in {"/analyze", "/bulk-analyze", "/export/csv", "/health"}:
        return jsonify({"error": "Not found"}), 404
    index_path = os.path.join(app.static_folder or "", "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({"status": "Backend running", "frontend": "not built"}), 200


@app.route("/")
def index():
    index_path = os.path.join(app.static_folder or "", "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({"status": "Backend running", "frontend": "not built"})


if __name__ == "__main__":
    print(f"HHT Vision API starting on :{PORT}  (gemini={'on' if GEMINI_KEY else 'demo'})")
    app.run(host="0.0.0.0", port=PORT)
