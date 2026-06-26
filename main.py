from Frontend.GUI import (
    GraphicalUserInterface,
    SetAssistantStatus,
    ShowTextToScreen,
    TempDirectoryPath,
    SetMicrophoneStatus,
    AnswerModifier,
    QueryModifier,
    GetMicrophoneStatus,
    GetAssistantStatus,
)
from Backend.Model import FirstLayerDMM
from Backend.RealtimeSearchEngine import RealtimeSearchEngine
from Backend.Automation import Automation
from Backend.SpeechToText import SpeechRecognition
from Backend.Chatbot import ChatBot
from Backend.TextToSpeech import TextToSpeech, request_stop  # ⬅️ import stop helper
from Backend.Database import log_usage, get_usage_summary
from dotenv import dotenv_values
from asyncio import run
from time import sleep
import subprocess
import threading
import json
import os
import time

# ────────────────────────────────────────────────────────────────────────────────
#  Config / Globals
# ────────────────────────────────────────────────────────────────────────────────
env_vars = dotenv_values(".env")
Username = env_vars.get("Username")
Assistantname = env_vars.get("Assistantname")

DefaultMessage = f"""{Username}: Hello {Assistantname}, How are you?
{Assistantname}: Welcome {Username}. I am doing well. How may I help you?"""

subprocesses = []
Functions = [
    "open",
    "close",
    "play",
    "system",
    "content",
    "google search",
    "youtube search",
    "write",
    "give",
    "read",
    "mute",
    "unmute",
    "volume up",
    "volume down",
]

# ────────────────────────────────────────────────────────────────────────────────
#  Helper routines
# ────────────────────────────────────────────────────────────────────────────────

def ShowDefaultChatIfNoChats():
    with open(r"Data/ChatLog.json", "r", encoding="utf-8") as file:
        if len(file.read()) < 5:
            with open(TempDirectoryPath("Database.data"), "w", encoding="utf-8") as f:
                f.write("")
            with open(TempDirectoryPath("Responses.data"), "w", encoding="utf-8") as f:
                f.write(DefaultMessage)


def ReadChatLogJson():
    with open(r"Data/ChatLog.json", "r", encoding="utf-8") as file:
        return json.load(file)


def ChatLogIntegration():
    json_data = ReadChatLogJson()
    formatted_chatlog = ""
    for entry in json_data:
        if entry["role"] == "user":
            formatted_chatlog += f"User: {entry['content']}\n"
        elif entry["role"] == "assistant":
            formatted_chatlog += f"Assistant: {entry['content']}\n"

    formatted_chatlog = (
        formatted_chatlog.replace("User", Username + " ")
        .replace("Assistant", Assistantname + " ")
        .strip()
    )

    with open(TempDirectoryPath("Database.data"), "w", encoding="utf-8") as file:
        file.write(AnswerModifier(formatted_chatlog))


def ShowChatsOnGUI():
    with open(TempDirectoryPath("Database.data"), "r", encoding="utf-8") as file:
        data = file.read()
    if data:
        lines = data.split("\n")
        result = "\n".join(lines)
        # GUI already watches file, nothing else to do


def InitialExecution():
    SetMicrophoneStatus("True")  # mic ON from the very start
    ShowTextToScreen("")
    ShowDefaultChatIfNoChats()
    ChatLogIntegration()
    ShowChatsOnGUI()


InitialExecution()

# ────────────────────────────────────────────────────────────────────────────────
#  Main Execution Loop
# ────────────────────────────────────────────────────────────────────────────────

def MainExecution():
    TaskExecution = False
    ImageExecution = False
    ImageGenerationQuery = ""

    # 1️⃣ Listen
    SetAssistantStatus("Listening…")
    Query = SpeechRecognition()

    # Skip if no speech was detected
    if not Query or not Query.strip():
        SetAssistantStatus("Available…")
        return True

    ShowTextToScreen(f" {Username}: {Query}")

    # 2️⃣ Think
    SetAssistantStatus("Thinking…")
    Decision = FirstLayerDMM(Query)
    print("\nDecision:", Decision, "\n")

    # 3️⃣ 👉 Handle STOP early
    if "stop" in [d.lower() for d in Decision]:
        request_stop()
        SetAssistantStatus("Stopped.")
        ShowTextToScreen(f" {Assistantname}: Okay, stopping.")
        # keep mic active so user can speak again immediately
        SetMicrophoneStatus("True")
        return True

    # 4️⃣ Extract info flags
    G = any(item.startswith("general") for item in Decision)
    R = any(item.startswith("realtime") for item in Decision)
    Mearged_query = " and ".join(
        [" ".join(i.split()[1:]) for i in Decision if i.startswith(("general", "realtime"))]
    )

    # 5️⃣ Automation / System commands
    for entry in Decision:
        if not TaskExecution:
            # patch: map "write" to "content"
            for i, q in enumerate(Decision):
                if q.lower().startswith("write "):
                    Decision[i] = "content " + q.split(" ", 1)[1]
            if any(entry.startswith(func) for func in Functions):
                run(Automation(list(Decision)))
                TaskExecution = True

    # 6️⃣ Image generation trigger
    for entry in Decision:
        if entry.lower().startswith(("generate image", "image", "draw")):
            ImageGenerationQuery = entry.split(" ", 2)[-1]
            ImageExecution = True
            break
    if ImageExecution:
        SetAssistantStatus("Generating image…")
        with open(r"Frontend/Files/ImageGeneration.data", "w", encoding="utf-8") as f:
            f.write(f"{ImageGenerationQuery},True")
        try:
            p = subprocess.Popen(
                ["python", r"Backend/ImageGeneration.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                shell=False,
            )
            subprocesses.append(p)
        except Exception as e:
            print("Error starting ImageGeneration.py:", e)
        # mic stays ON, assistant keeps listening while image renders
        SetMicrophoneStatus("True")
        return True

    # 7️⃣ Answer logic
    _t0 = time.time()

    if (G and R) or R:
        SetAssistantStatus("Searching…")
        try:
            Answer = RealtimeSearchEngine(QueryModifier(Mearged_query))
            _ms = int((time.time() - _t0) * 1000)
            log_usage(Query, "realtime", _ms, "success")
        except Exception as e:
            _ms = int((time.time() - _t0) * 1000)
            log_usage(Query, "realtime", _ms, "error", str(e))
            Answer = "Sorry, I encountered an error while searching. Please try again."
        ShowTextToScreen(f" {Assistantname}: {Answer}")
        SetAssistantStatus("Answering…")
        TextToSpeech(Answer)
        SetMicrophoneStatus("True")  # immediately reopen mic
        return True

    for entry in Decision:
        if entry.startswith("general"):
            SetAssistantStatus("Thinking…")
            query_final = entry.replace("general", "").strip()
            try:
                Answer = ChatBot(QueryModifier(query_final))
                _ms = int((time.time() - _t0) * 1000)
                log_usage(Query, "general", _ms, "success")
            except Exception as e:
                _ms = int((time.time() - _t0) * 1000)
                log_usage(Query, "general", _ms, "error", str(e))
                Answer = "Sorry, I encountered an error. Please try again."
            ShowTextToScreen(f" {Assistantname}: {Answer}")
            SetAssistantStatus("Answering…")
            TextToSpeech(Answer)
            SetMicrophoneStatus("True")
            return True
        elif entry.startswith("realtime"):
            SetAssistantStatus("Searching…")
            query_final = entry.replace("realtime", "").strip()
            try:
                Answer = RealtimeSearchEngine(QueryModifier(query_final))
                _ms = int((time.time() - _t0) * 1000)
                log_usage(Query, "realtime", _ms, "success")
            except Exception as e:
                _ms = int((time.time() - _t0) * 1000)
                log_usage(Query, "realtime", _ms, "error", str(e))
                Answer = "Sorry, I encountered an error while searching. Please try again."
            ShowTextToScreen(f" {Assistantname}: {Answer}")
            SetAssistantStatus("Answering…")
            TextToSpeech(Answer)
            SetMicrophoneStatus("True")
            return True
        elif entry == "exit":
            request_stop()
            Answer = "Okay, Bye!"
            ShowTextToScreen(f" {Assistantname}: {Answer}")
            SetAssistantStatus("Answering…")
            TextToSpeech(Answer)
            sleep(0.5)
            os._exit(0)


# ────────────────────────────────────────────────────────────────────────────────
#  Thread wrappers
# ────────────────────────────────────────────────────────────────────────────────

def FirstThread():
    while True:
        if GetMicrophoneStatus() == "True":
            MainExecution()
        else:
            if "Available…" not in GetAssistantStatus():
                SetAssistantStatus("Available…")
            sleep(0.05)


def SecondThread():
    GraphicalUserInterface()


# ────────────────────────────────────────────────────────────────────────────────
#  Entry‑point
# ────────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    thread1 = threading.Thread(target=FirstThread, daemon=True)
    thread1.start()
    SecondThread()
