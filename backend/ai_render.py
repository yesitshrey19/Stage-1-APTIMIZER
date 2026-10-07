"""Google Nano Banana & Gemini AI 3D Architectural Render Engine.

Provides photorealistic AI-generated architectural renders for towers and floor plans
using Google Nano Banana (models/nano-banana-pro-preview) and Gemini image models,
as well as procedural 3D isometric cutaway visualizations.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Primary Google AI Studio Image Models
NANO_BANANA_MODEL = "models/nano-banana-pro-preview"
FALLBACK_IMAGE_MODELS = [
    "models/gemini-3-pro-image",
    "models/gemini-2.5-flash-image",
    "models/gemini-3.1-flash-image-preview",
]

# Cache directory for persistent renders
RENDERS_DIR = Path(__file__).resolve().parent / "data" / "ai_renders"
RENDERS_DIR.mkdir(parents=True, exist_ok=True)


def _get_gemini_key() -> str:
    """Retrieve Gemini API key from environment."""
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def build_architectural_prompt(
    tower: Dict[str, Any],
    project_name: str = "",
    view_type: str = "floorplan_3d",
    custom_notes: str = "",
) -> str:
    """Synthesize a rich, architecturally precise prompt from tower parameters."""
    tname = tower.get("name") or "Residential Tower"
    pname = project_name or "Luxury Residential Development"
    floors = int(tower.get("floors") or 14)
    height_m = round(floors * 3.15, 1)
    footprint = round(float(tower.get("footprint_area") or 600.0), 1)
    corridor_w = tower.get("corridor_width") or 2.0

    # Inspect floor layout rooms if available
    floor_layouts = tower.get("floor_layouts") or {}
    rooms = []
    if "1" in floor_layouts:
        rooms = (floor_layouts["1"] or {}).get("rooms") or []
    elif 1 in floor_layouts:
        rooms = (floor_layouts[1] or {}).get("rooms") or []
    elif floor_layouts:
        first_k = next(iter(floor_layouts))
        rooms = (floor_layouts[first_k] or {}).get("rooms") or []

    room_types = {r.get("type", "room") for r in rooms}
    features = []
    if "bedroom" in room_types:
        features.append("master suites with ensuite spa bathrooms and private balconies")
    if "kitchen" in room_types:
        features.append("open-concept Italian kitchens with marble waterfall islands")
    if "pooja" in room_types:
        features.append("traditional sacred mandir pooja niche")
    if "study" in room_types or "office" in room_types:
        features.append("dedicated executive home office / study library")
    if "balcony" in room_types:
        features.append("cantilevered viewing terraces with frameless glass balustrades")

    features_str = ", ".join(features) if features else "luxury apartment floor plate with panoramic balconies"

    if view_type == "exterior_3d":
        prompt = (
            f"Hyper-realistic professional architectural photography of '{tname}', a {floors}-storey "
            f"({height_m}m high) luxury residential skyscraper in '{pname}'. "
            f"Sleek modern parametric facade with floor-to-ceiling Low-E acoustic curtain wall glazing, "
            f"elegant cantilevered balconies with warm timber soffits and tempered glass railings, "
            f"vertical landscaped sky gardens on intermediate terrace levels. "
            f"Warm dramatic dusk twilight lighting, amber interior illumination glowing warmly from high-end apartments, "
            f"water reflection pool and mature date palms at the double-height stone podium entry lobby. "
            f"Shot on Hasselblad H6D-100c medium format, 35mm architectural lens, f/8, crisp structural lines, "
            f"8k resolution, award-winning Architectural Digest cover quality."
        )
    elif view_type == "interior_living":
        prompt = (
            f"High-end luxury architectural interior photography of the main living, dining, and balcony space in '{tname}', "
            f"'{pname}'. Panoramic floor-to-ceiling glass windows overlooking city skyline at golden hour sunset. "
            f"Contemporary Scandinavian luxury aesthetic: light Chevron oak hardwood parquet flooring, "
            f"curved Italian bouclé sectional sofa, travertine coffee table, Flos architectural recessed LED lighting, "
            f"minimalist Calacatta marble dining table seating 8, fluted walnut accent wall with integrated media credenza, "
            f"sliding glass pocket doors opening onto an expansive outdoor terrace with timber decking and lush potted plants. "
            f"Photorealistic 8k, soft cinematic ambient daylight, architectural magazine feature."
        )
    else:
        # Default: "floorplan_3d" - 3D Isometric Cutaway Floor Plan
        prompt = (
            f"Hyper-detailed 3D isometric cutaway architectural floor plan render of a typical floor plate in '{tname}', '{pname}'. "
            f"Bird's-eye axonometric 45-degree angle showing fully furnished modern apartments ({footprint} sq.m footprint). "
            f"Extruded architectural white poché partition walls with clean cutaway heights. "
            f"Fully furnished spaces: {features_str}. "
            f"Light French natural oak flooring in living areas, honed marble tiles in bathrooms, "
            f"contemporary designer furniture blocks, king beds with soft linen bedding, sectional living sofas, dining sets. "
            f"Soft warm diffused daylight streaming through full-height perimeter window openings, casting subtle soft contact shadows. "
            f"Clean architectural model diorama aesthetics, white background, ultra-sharp 8k render, octane render style."
        )

    if custom_notes:
        prompt += f" Additional specifications: {custom_notes.strip()}."

    return prompt


async def generate_ai_render(
    tower: Dict[str, Any],
    project_id: str,
    project_name: str = "",
    view_type: str = "floorplan_3d",
    custom_prompt: Optional[str] = None,
    model: str = NANO_BANANA_MODEL,
) -> Dict[str, Any]:
    """Request an AI 3D architectural render using Google Nano Banana or Gemini image models.

    Handles quota limits (HTTP 429) gracefully and caches successful renders.
    """
    api_key = _get_gemini_key()
    tower_id = str(tower.get("id") or "tower")
    tname = tower.get("name") or "Tower"

    # Determine prompt
    prompt = (
        custom_prompt.strip()
        if custom_prompt and custom_prompt.strip()
        else build_architectural_prompt(tower, project_name=project_name, view_type=view_type)
    )

    if not api_key:
        return {
            "status": "no_key",
            "view_type": view_type,
            "model": model,
            "prompt": prompt,
            "has_image": False,
            "message": "No GEMINI_API_KEY found in backend/.env. Please configure your Google AI Studio API key.",
            "setup_url": "https://aistudio.google.com/apikey",
        }

    # Ensure model starts with models/
    clean_model = model if model.startswith("models/") else f"models/{model}"

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/{clean_model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"]
        }
    }

    cache_file = RENDERS_DIR / f"{project_id}_{tower_id}_{view_type}.png"
    meta_file = RENDERS_DIR / f"{project_id}_{tower_id}_{view_type}.json"

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(endpoint, json=payload)

        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return {
                    "status": "error",
                    "view_type": view_type,
                    "model": clean_model,
                    "prompt": prompt,
                    "has_image": False,
                    "message": "Google AI returned empty candidate list.",
                }

            parts = candidates[0].get("content", {}).get("parts") or []
            img_b64 = None
            for p in parts:
                inline = p.get("inlineData") or {}
                if inline.get("data"):
                    img_b64 = inline.get("data")
                    break

            if not img_b64:
                # Some models might return text or URL
                text_content = " ".join(p.get("text", "") for p in parts)
                return {
                    "status": "text_only",
                    "view_type": view_type,
                    "model": clean_model,
                    "prompt": prompt,
                    "has_image": False,
                    "text_output": text_content,
                    "message": "Model generated description instead of binary image data.",
                }

            # Save binary PNG to disk
            raw_bytes = base64.b64decode(img_b64)
            cache_file.write_bytes(raw_bytes)

            meta = {
                "project_id": project_id,
                "tower_id": tower_id,
                "tower_name": tname,
                "view_type": view_type,
                "model": clean_model,
                "prompt": prompt,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "file_size": len(raw_bytes),
            }
            meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

            return {
                "status": "success",
                "view_type": view_type,
                "model": clean_model,
                "prompt": prompt,
                "has_image": True,
                "image_data_uri": f"data:image/png;base64,{img_b64}",
                "image_url": f"/api/projects/{project_id}/towers/{tower_id}/ai-render-image?view_type={view_type}&t={int(datetime.now().timestamp())}",
                "message": f"Successfully generated 3D architectural render using {clean_model} (Google Nano Banana).",
            }

        elif resp.status_code == 429:
            # Quota or billing limit
            err_data = {}
            try:
                err_data = resp.json().get("error") or {}
            except Exception:
                pass

            err_msg = err_data.get("message", "Quota exceeded.")
            is_limit_zero = "limit: 0" in err_msg or "free_tier" in err_msg

            return {
                "status": "quota_exceeded",
                "view_type": view_type,
                "model": clean_model,
                "prompt": prompt,
                "has_image": cache_file.exists(),
                "cached_image_url": (
                    f"/api/projects/{project_id}/towers/{tower_id}/ai-render-image?view_type={view_type}"
                    if cache_file.exists()
                    else None
                ),
                "error": "Google AI Studio Image Quota Limit (Limit: 0 on Free Tier)",
                "detail": (
                    "Google AI Studio restricts image generation models (Nano Banana / Gemini Image) on free tier keys (quota limit = 0). "
                    "To generate live AI renders with your API key, enable Pay-as-you-go billing in Google AI Studio or Google Cloud Console. "
                    "In the meantime, you can copy the engineered prompt below to run directly in Google AI Studio / Imagen, "
                    "or explore the interactive 2D presentation floor plans."
                ),
                "is_limit_zero": is_limit_zero,
                "google_message": err_msg,
                "setup_url": "https://aistudio.google.com",
            }

        else:
            return {
                "status": "api_error",
                "view_type": view_type,
                "model": clean_model,
                "prompt": prompt,
                "has_image": cache_file.exists(),
                "error": f"HTTP {resp.status_code}",
                "message": resp.text[:300],
            }

    except Exception as exc:
        logger.exception("Error generating AI render: %s", exc)
        return {
            "status": "error",
            "view_type": view_type,
            "model": clean_model,
            "prompt": prompt,
            "has_image": cache_file.exists(),
            "error": str(exc),
            "message": f"Connection or generation failed: {exc}",
        }


def get_cached_render_bytes(project_id: str, tower_id: str, view_type: str = "floorplan_3d") -> Optional[bytes]:
    """Retrieve cached PNG render bytes if present."""
    cache_file = RENDERS_DIR / f"{project_id}_{tower_id}_{view_type}.png"
    if cache_file.exists():
        return cache_file.read_bytes()
    return None


def get_cached_render_meta(project_id: str, tower_id: str, view_type: str = "floorplan_3d") -> Optional[Dict[str, Any]]:
    """Retrieve metadata for a cached render."""
    meta_file = RENDERS_DIR / f"{project_id}_{tower_id}_{view_type}.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None
