import os
import sys
import asyncio
import requests
import base64
from random import randint
from time import sleep
from dotenv import load_dotenv
from PIL import Image

# Add project root to sys.path to resolve Frontend module
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from Frontend.GUI import TempDirectoryPath, ShowTextToScreen, SetAssistantStatus

load_dotenv()
STABILITY_KEY = os.getenv("STABILITY_API_KEY")
if not STABILITY_KEY:
    raise ValueError("Set STABILITY_API_KEY in .env with inference permissions.")

API_URL = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
headers = {
    "Authorization": f"Bearer {STABILITY_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

os.makedirs("Data", exist_ok=True)

async def query_stability(prompt):
    payload = {
        "text_prompts": [{"text": prompt, "weight": 1}],
        "cfg_scale": 7.5,
        "steps": 40,
        "width": 1024,
        "height": 1024,
        "samples": 4  # generate 4 images
    }
    try:
        resp = await asyncio.to_thread(requests.post, API_URL, headers=headers, json=payload)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"API error {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        print(f"API request failed: {e}")
        return None

def save_and_show(prompt, data):
    saved_paths = []
    for index, artifact in enumerate(data.get("artifacts", []), start=1):
        try:
            img_bytes = base64.b64decode(artifact["base64"])
            name = f"{prompt.replace(' ', '_')}_{randint(1000, 9999)}_{index}.png"
            path = os.path.join("Data", name)
            with open(path, "wb") as f:
                f.write(img_bytes)
            print(f"Saved image: {path}")
            img = Image.open(path)
            img.show()
            saved_paths.append(path)
            sleep(1)
        except Exception as e:
            print(f"Error processing image {index}: {e}")
    return saved_paths

def GenerateImages(prompt):
    data = asyncio.run(query_stability(prompt))
    if data:
        paths = save_and_show(prompt, data)
        if paths:
            # Update GUI with success message
            ShowTextToScreen(f" Assistant: Generated images saved at: {', '.join(paths)}")
            SetAssistantStatus("Image generation complete.")
            return True
        else:
            ShowTextToScreen(" Assistant: Failed to generate images.")
            SetAssistantStatus("Image generation failed.")
            return False
    else:
        ShowTextToScreen(" Assistant: Image generation failed due to API error.")
        SetAssistantStatus("Image generation failed.")
        return False

# === Monitor "Frontend/Files/ImageGeneration.data" ===
data_file_path = TempDirectoryPath("ImageGeneration.data")

while True:
    try:
        with open(data_file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content or "," not in content:
            sleep(0.1)
            continue

        # Split only on the last comma to handle prompts containing commas
        *prompt_parts, status = content.rsplit(",", 1)
        prompt = ",".join(prompt_parts).strip()
        status = status.strip()

        if status == "True" and prompt:
            print(f"➡️ Generating images for prompt: '{prompt}'")
            GenerateImages(prompt)
            # Reset the file to allow new requests
            with open(data_file_path, "w", encoding="utf-8") as f:
                f.write("")
        sleep(0.1)
    except FileNotFoundError:
        # File may not exist initially; create it
        with open(data_file_path, "w", encoding="utf-8") as f:
            f.write("")
        sleep(0.1)
    except Exception as e:
        print(f"File check error: {e}")
        ShowTextToScreen(f" Assistant: Image generation error: {str(e)}")
        SetAssistantStatus("Image generation error.")
        sleep(0.1)