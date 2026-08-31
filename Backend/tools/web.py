"""Web search and app/website opening tools.

web_search is what used to be Backend/RealtimeSearchEngine.py's fixed
pre-search step (every "realtime" query always searched, whether it needed
to or not). Now it's a tool the agent decides to call — a plain
conversational question never touches the network, a question that needs
current information does, and the agent can call it more than once in a
row if the first search wasn't enough.

open_app ports Backend/Automation.py's OpenApp() unchanged in behavior:
try a locally-installed app first, then treat the name as a domain, then
fall back to a Google-search-and-open-first-result guess.
"""


import webbrowser

import requests
from AppOpener import close as appclose
from AppOpener import open as appopen
from bs4 import BeautifulSoup
from ddgs import DDGS  # duckduckgo_search was renamed to ddgs upstream; the

# old package name was also returning zero results
# by the time this was tested (2026-08-25)
from Backend.tools.registry import tool

_USERAGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36"
)
_session = requests.Session()


@tool(
    "Search the web for current information. Use this for news, prices, "
    "weather, sports scores, or anything that could have changed after "
    "your training data — do not use it for questions you can already "
    "answer confidently."
)
def web_search(query: str, num_results: int = 5) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return f"No search results found for '{query}'."

    lines = [f"Search results for '{query}':"]
    for r in results:
        lines.append(f"- {r.get('title', '')}: {r.get('body', '')}")
    return "\n".join(lines)


@tool("Open a website or an installed desktop application by name, e.g. 'chrome', 'notepad', 'spotify'.")
def open_app(name: str) -> str:
    name_clean = name.strip().lower().replace(" ", "")

    # Try a locally-installed application first (fast path).
    try:
        appopen(name, match_closest=True, output=True, throw_error=True)
        return f"Opened installed app: {name}"
    except Exception:
        pass

    # Already a URL / domain-like string.
    if any(ext in name_clean for ext in [".com", ".org", ".net", ".in", ".edu"]):
        url = name_clean if name_clean.startswith("http") else f"https://{name_clean}"
        webbrowser.open(url)
        return f"Opened URL: {url}"

    # Guess it's a well-known .com domain.
    domain_guess = f"https://www.{name_clean}.com"
    try:
        response = _session.head(domain_guess, timeout=3)
        if response.status_code < 400:
            webbrowser.open(domain_guess)
            return f"Opened site: {domain_guess}"
    except Exception:
        pass

    # Last resort: Google it and open the first real result.
    try:
        search_url = f"https://www.google.com/search?q={name}"
        response = _session.get(search_url, headers={"User-Agent": _USERAGENT}, timeout=5)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                # bs4 types an attribute value as str | list[str] (some
                # HTML attributes, like class, are multi-valued) — href
                # never actually is, so this is a safe, always-correct cast.
                href = str(link["href"])
                if href.startswith("http"):
                    webbrowser.open(href)
                    return f"Opened top search result for '{name}': {href}"
    except Exception as e:
        return f"Error: could not open '{name}': {e}"

    return f"Error: could not find or open '{name}'."


@tool("Close a running application by name, e.g. 'notepad', 'spotify'.")
def close_app(name: str) -> str:
    if "chrome" in name.lower():
        return "Skipping close: closing all Chrome windows is disabled to avoid losing the user's other tabs."
    try:
        appclose(name, match_closest=True, output=True, throw_error=True)
        return f"Closed {name}."
    except Exception as e:
        return f"Could not close '{name}': {e}"


@tool("Open a Google search results page in the browser for a topic (does not read or summarize the results).")
def google_search(topic: str) -> str:
    webbrowser.open(f"https://www.google.com/search?q={topic}")
    return f"Opened Google search for '{topic}'."
