from AppOpener import close, open as appopen
from webbrowser import open as webopen
from pywhatkit import search, playonyt
from dotenv import dotenv_values
from bs4 import BeautifulSoup
from rich import print
from groq import Groq
import webbrowser
import subprocess
import requests
import keyboard
import asyncio
import os

# Load environment variables
env_vars = dotenv_values(".env")
GroqAPIKey = env_vars.get("GroqAPIKey")

# Define user agent and HTML classes
useragent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36'
classes = ["zCubwf", "hgKElc", "LTKOO SY7ric", "ZOLcW", "gsrt vk_bk FzvWSb YwPhnf", "pclqee", 
           "tw-Data-text tw-text-small tw-ta", "IZ6rdc", "05uR6d LTKOO", "vlzY6d", 
           "webanswers-webanswers_table_webanswers-table", "dDoNo ikb4Bb gsrt", "sXLa0e",
           "LWkfKe", "VQF4g", "qv3Wpe", "kno-rdesc", "SPZz6b"]

# Initialize Groq client
client = Groq(api_key=GroqAPIKey)
messages = []

SystemChatBot = [{
    "role": "system",
    "content": f"Hello, I am your assistant. You're a content writer. You have to write content like letters, codes, applications, essays, etc. based on the user's request. Always respond in a professional manner and ensure that your content is well-structured and clear."
}]

def GoogleSearch(Topic):
    search(Topic)
    return True

def Content(Topic):
    def OpenNotepad(File):
        default_text_editor = 'notepad.exe'
        subprocess.Popen([default_text_editor, File])

    def ContentWriterAI(prompt):
        messages.append({"role": "user", "content": f"{prompt}"})
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=SystemChatBot + messages,
            max_tokens=2048,
            temperature=0.7,
            top_p=1,
            stream=True,
            stop=None
        )
        Answer = ""
        for chunk in completion:
            if chunk.choices[0].delta.content:
                Answer += chunk.choices[0].delta.content

        Answer = Answer.replace("</s>", "")
        messages.append({"role": "assistant", "content": Answer})
        return Answer

    Topic = Topic.replace("Content", "")
    ContentByAI = ContentWriterAI(Topic)

    file_path = rf"Data\{Topic.lower().replace(' ', '')}.txt"
    with open(file_path, "w", encoding='utf-8') as file:
        file.write(ContentByAI)

    OpenNotepad(file_path)
    return True

def YouTubeSearch(Topic):
    Url4Search = f"https://www.youtube.com/results?search_query={Topic}"
    webbrowser.open(Url4Search)
    return True

def PlayYoutube(query):
    playonyt(query)
    return True

def OpenApp(app_name, sess=requests.session()):
    from urllib.parse import urlparse

    app_name_clean = app_name.strip().lower().replace(" ", "")

    # ✅ Try local application first (fast)
    try:
        appopen(app_name, match_closest=True, output=True, throw_error=True)
        print(f"[INFO] Opened installed app: {app_name}")
        return True
    except Exception:
        print(f"[INFO] '{app_name}' not found locally. Trying as website...")

    # ✅ Fast guess: is this already a URL or domain-like?
    if any(ext in app_name_clean for ext in [".com", ".org", ".net", ".in", ".edu"]):
        if not app_name_clean.startswith("http"):
            app_name_clean = "https://" + app_name_clean
        webbrowser.open(app_name_clean)
        print(f"[INFO] Opened URL: {app_name_clean}")
        return True

    # ✅ Smart guess: treat as domain name
    domain_guess = f"https://www.{app_name_clean}.com"
    try:
        response = sess.head(domain_guess, timeout=2)
        if response.status_code < 400:
            webbrowser.open(domain_guess)
            print(f"[INFO] Guessed and opened site: {domain_guess}")
            return True
    except:
        pass

    # ✅ Final fallback (if needed): Google search
    try:
        search_url = f"https://www.google.com/search?q={app_name}"
        headers = {"User-Agent": useragent}
        response = sess.get(search_url, headers=headers, timeout=3)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.startswith("http"):
                    webbrowser.open(href)
                    print(f"[INFO] Fallback Google result: {href}")
                    return True
        print("[WARN] Google search returned no usable link.")
    except Exception as e:
        print(f"[ERROR] Google search failed: {e}")

    return False

def CloseApp(app):
    if "Chrome" in app:
        pass
    else:
        try:
            close(app, match_closest=True, output=True, throw_error=True)
            return True
        except:
            return False

def System(command):
    def mute():
        keyboard.press_and_release("volume mute")
    def unmute():
        keyboard.press_and_release("volume mute")
    def volume_up():
        keyboard.press_and_release("volume up")
    def volume_down():
        keyboard.press_and_release("volume down")

    if command == "mute":
        mute()
    elif command == "unmute":
        unmute()
    elif command == "volume up":
        volume_up()
    elif command == "volume down":
        volume_down()
    return True

async def TranslateAndExecute(commands: list[str]):
    # ✅ Patch: Normalize 'write' commands to trigger content writing
    for i, cmd in enumerate(commands):
        if cmd.strip().lower().startswith("write "):
            commands[i] = "content " + cmd

    funcs = []
    for command in commands:
        if command.startswith("open"):
            if "open it" in command or "open file" in command:
                continue
            fun = asyncio.to_thread(OpenApp, command.removeprefix("open "))
            funcs.append(fun)
        elif command.startswith("general"):
            pass
        elif command.startswith("realtime "):
            pass
        elif command.startswith("close"):
            fun = asyncio.to_thread(CloseApp, command.removeprefix("close"))
            funcs.append(fun)
        elif command.startswith("play "):
            fun = asyncio.to_thread(PlayYoutube, command.removeprefix("play "))
            funcs.append(fun)
        elif command.startswith("google search "):
            fun = asyncio.to_thread(GoogleSearch, command.removeprefix("google search "))
            funcs.append(fun)
        elif command.startswith("system "):
            fun = asyncio.to_thread(System, command.removeprefix("system "))
            funcs.append(fun)
        elif command.startswith("content "):
            fun = asyncio.to_thread(Content, command.removeprefix("content "))
            funcs.append(fun)
        elif command.startswith("youtube search "):
            fun = asyncio.to_thread(YouTubeSearch, command.removeprefix("youtube search "))
            funcs.append(fun)
        else:
            print(f"No Function Found for {command}")

    results = await asyncio.gather(*funcs)
    for result in results:
        if isinstance(result, str):
            yield result
        else:
            yield result

async def Automation(commands: list[str]):
    async for result in TranslateAndExecute(commands):
        pass
    return True
