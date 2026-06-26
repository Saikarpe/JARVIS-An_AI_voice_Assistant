import speech_recognition as sr
from dotenv import dotenv_values
import os
import mtranslate as mt

# Load environment variables from the .env file.
env_vars = dotenv_values(".env")
InputLanguage = env_vars.get("InputLanguage", "en")

# Temp dir path for assistant status
current_dir = os.getcwd()
TempDirPath = f"{current_dir}/Frontend/Files"
os.makedirs(TempDirPath, exist_ok=True)

# Create recognizer instance
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = 1.5  # seconds of silence before phrase is considered complete

# Find the correct microphone (Realtek Audio, not virtual Camo mic)
MIC_INDEX = None
try:
    mic_names = sr.Microphone.list_microphone_names()
    # Prefer Realtek Audio mic
    for i, name in enumerate(mic_names):
        if "Realtek" in name and ("Mic" in name or "input" in name.lower()):
            MIC_INDEX = i
            print(f"[STT] Using microphone [{i}]: {name}")
            break
    # Fallback: try any non-virtual, non-output mic
    if MIC_INDEX is None:
        for i, name in enumerate(mic_names):
            name_lower = name.lower()
            if "microphone" in name_lower and "camo" not in name_lower and "output" not in name_lower and "speaker" not in name_lower:
                MIC_INDEX = i
                print(f"[STT] Using microphone [{i}]: {name}")
                break
    if MIC_INDEX is None:
        print("[STT] WARNING: Could not find a suitable mic, using system default")
except Exception as e:
    print(f"[STT] Error listing mics: {e}")

def SetAssistantStatus(Status):
    with open(f'{TempDirPath}/Status.data', "w", encoding='utf-8') as file:
        file.write(Status)

def QueryModifier(Query):
    new_query = Query.lower().strip()
    query_words = new_query.split()
    question_words = ["how", "what", "who", "where", "when", "why", "which", "whose", "whom", "can you", "what's", "where's", "how's"]

    if any(word + " " in new_query for word in question_words):
        new_query = new_query.rstrip(".?!") + "?"
    else:
        new_query = new_query.rstrip(".?!") + "."

    return new_query.capitalize()

def UniversalTranslator(Text):
    english_translation = mt.translate(Text, "en", "auto")
    return english_translation.capitalize()

def SpeechRecognition():
    """Listen to the microphone and return the recognized text."""
    mic_kwargs = {"device_index": MIC_INDEX} if MIC_INDEX is not None else {}
    
    with sr.Microphone(**mic_kwargs) as source:
        SetAssistantStatus("Listening...")
        # Brief ambient noise calibration
        recognizer.adjust_for_ambient_noise(source, duration=0.5)

        try:
            # Listen with a timeout so it doesn't hang forever
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)
        except sr.WaitTimeoutError:
            # No speech detected within timeout, return empty to retry
            SetAssistantStatus("Available...")
            return ""

    SetAssistantStatus("Recognizing...")
    try:
        # Use Google's free speech recognition API
        if InputLanguage.lower() == "en" or "en" in InputLanguage.lower():
            Text = recognizer.recognize_google(audio, language="en-IN")
        else:
            Text = recognizer.recognize_google(audio, language=InputLanguage)

        if not Text or not Text.strip():
            return ""

        print(f"[STT] Heard: {Text}")

        if InputLanguage.lower() == "en" or "en" in InputLanguage.lower():
            return QueryModifier(Text)
        else:
            SetAssistantStatus("Translating...")
            return QueryModifier(UniversalTranslator(Text))

    except sr.UnknownValueError:
        # Could not understand audio
        print("[STT] Could not understand audio")
        return ""
    except sr.RequestError as e:
        print(f"[STT] Google API error: {e}")
        return ""
    except Exception as e:
        print(f"[STT] Unexpected error: {e}")
        return ""

# Run the speech recognition loop
if __name__ == "__main__":
    while True:
        Text = SpeechRecognition()
        if Text:
            print(f"You said: {Text}")
