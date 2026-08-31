"""System control tools (audio, etc.)."""

import keyboard

from Backend.tools.registry import tool

_ACTIONS = {
    "mute": "volume mute",
    "unmute": "volume mute",  # same hardware key toggles both directions
    "volume_up": "volume up",
    "volume_down": "volume down",
}


@tool("Control system audio. action must be exactly one of: mute, unmute, volume_up, volume_down.")
def control_audio(action: str) -> str:
    key = _ACTIONS.get(action.lower().strip())
    if not key:
        return f"Error: unknown action '{action}'. Must be one of: {', '.join(_ACTIONS)}"
    keyboard.press_and_release(key)
    return f"Done: {action}"
