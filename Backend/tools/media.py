"""YouTube tools.

The old Backend/Automation.py used pywhatkit.playonyt() to "play" a song:
under the hood it opens a browser, waits a fixed number of seconds, then
drives the mouse/keyboard via pyautogui to click play — fragile (breaks if
the page layout differs or the window isn't focused), slow, and pywhatkit
also does a blocking network call at *import time* just to check the
system clock, which was adding multiple seconds to every app startup.

Opening the direct watch URL for the top search result is faster, doesn't
touch the mouse, and doesn't carry that import-time cost.
"""

import webbrowser

from ddgs import DDGS  # see Backend/tools/web.py for why this isn't duckduckgo_search

from Backend.tools.registry import tool


@tool("Play a song or video on YouTube by opening the top search result directly.")
def play_youtube(query: str) -> str:
    video_url = _find_first_youtube_video(query)
    if video_url:
        webbrowser.open(video_url)
        return f"Playing: {video_url}"
    # Fall back to a search results page if we couldn't resolve a direct link.
    webbrowser.open(f"https://www.youtube.com/results?search_query={query}")
    return f"Couldn't resolve a direct video link for '{query}'; opened search results instead."


@tool("Open YouTube search results for a topic without playing anything.")
def search_youtube(query: str) -> str:
    webbrowser.open(f"https://www.youtube.com/results?search_query={query}")
    return f"Opened YouTube search for '{query}'."


def _find_first_youtube_video(query: str) -> str:
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(f"{query} site:youtube.com/watch", max_results=5):
                href = r.get("href", "")
                if "youtube.com/watch" in href:
                    return href
    except Exception:
        pass
    return ""
