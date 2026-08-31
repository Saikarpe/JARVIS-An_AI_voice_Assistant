"""Image generation tool.

Ports Backend/ImageGeneration.py's Stability AI call in-process. The old
version ran as a *subprocess*, spawned by main.py, that communicated back
by writing Frontend/Files/ImageGeneration.data and polling it in a `while
True: sleep(0.1)` loop — the exact file-IPC pattern the rest of this
project moved away from in Phase 1. As a plain tool function the agent
calls directly, there's no subprocess to spawn, clean up, or leak.
"""

import base64
import logging
import os
from random import randint
from time import sleep

import requests
from dotenv import dotenv_values
from PIL import Image

from Backend.tools.registry import tool

logger = logging.getLogger(__name__)

env_vars = dotenv_values(".env")
_STABILITY_KEY = env_vars.get("STABILITY_API_KEY")
_API_URL = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"


@tool("Generate one or more images from a text prompt and open them for the user.")
def generate_image(prompt: str, count: int = 1) -> str:
    if not _STABILITY_KEY:
        return "Error: STABILITY_API_KEY is not set in .env — image generation is unavailable."

    count = max(1, min(count, 4))  # Stability's SDXL endpoint caps samples at 4
    headers = {
        "Authorization": f"Bearer {_STABILITY_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "text_prompts": [{"text": prompt, "weight": 1}],
        "cfg_scale": 7.5,
        "steps": 40,
        "width": 1024,
        "height": 1024,
        "samples": count,
    }

    try:
        resp = requests.post(_API_URL, headers=headers, json=payload, timeout=60)
    except Exception as e:
        return f"Error: image generation request failed: {e}"

    if resp.status_code != 200:
        return f"Error: Stability API returned {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    os.makedirs("Data", exist_ok=True)
    saved_paths = []
    for index, artifact in enumerate(data.get("artifacts", []), start=1):
        try:
            img_bytes = base64.b64decode(artifact["base64"])
            name = f"{prompt.replace(' ', '_')[:40]}_{randint(1000, 9999)}_{index}.png"
            path = os.path.join("Data", name)
            with open(path, "wb") as f:
                f.write(img_bytes)
            Image.open(path).show()
            saved_paths.append(path)
            sleep(0.5)  # stagger opening each image viewer window
        except Exception as e:
            logger.warning("error saving artifact %d: %s", index, e)

    if not saved_paths:
        return "Error: image generation returned no usable images."
    return f"Generated {len(saved_paths)} image(s): {', '.join(saved_paths)}"
