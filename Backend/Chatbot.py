from groq import Groq # Importing the Groq library to use
from json import load, dump # Importing functions for backwards compatibility
import datetime # Importing the datetime module for real-time info
from dotenv import dotenv_values
from Backend.Database import save_message, get_chat_history, clear_chat_history

env_vars = dotenv_values(".env")

Username = env_vars.get("Username")
Assistantname = env_vars.get("Assistantname")
GroqAPIKey = env_vars.get("GroqAPIKey")

client = Groq(api_key=GroqAPIKey)

# Define a system message that provides context to the AI
System = f"""Hello, I am {Username}, You are a very accurate and advanced AI chatbot named {Assistantname} which also has real-time up-to-date information from the internet.
*** Do not tell time until I ask, do not talk too much, just answer the question.***
*** Reply in only English, even if the question is in Hindi, reply in English.***
*** Do not provide notes in the output, just answer the question and never mention your training data. ***
"""
SystemChatBot = [
    {"role": "system", "content": System}
]

# Keep ChatLog.json path for backwards compatibility with GUI
CHATLOG_PATH = r"Data/ChatLog.json"

def RealtimeInformation():
    current_date_time = datetime.datetime.now()
    day = current_date_time.strftime("%A")
    date = current_date_time.strftime("%d")
    month = current_date_time.strftime("%B")
    year = current_date_time.strftime("%Y")
    hour = current_date_time.strftime("%H")
    minute = current_date_time.strftime("%M")
    second = current_date_time.strftime("%S")

    data = f"Please use this real time information if needed:\n"
    data += f"Day : {day}\nDate : {date}\nMonth : {month}\nYear : {year}\n"
    data += f"Time : {hour} hours :{minute} minutes : {second} seconds.\n"
    return data

def AnswerModifier(Answer):
    lines = Answer.split('\n') # Split the response into lines.
    non_empty_lines = [line for line in lines if line.strip()] # Remove empty lines.
    modified_answer = '\n'.join(non_empty_lines) # Join the cleaned lines back together.
    return modified_answer

# Main chatbot function to handle user queries.

def ChatBot(Query):
    """ This function sends the user's query to the chatbot and returns the AI's response."""

    try:
        # Get chat history from SQLite database
        messages = get_chat_history(limit=30)

        messages.append({"role": "user", "content": f" {Query}"})
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=SystemChatBot + [{"role": "system", "content": RealtimeInformation()}] + messages,
            max_tokens=1024,
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

        # Save both user query and assistant response to the database
        save_message("user", Query)
        save_message("assistant", Answer)

        # Also update ChatLog.json for backwards compatibility with GUI
        try:
            json_messages = get_chat_history(limit=100)
            with open(CHATLOG_PATH, "w") as f:
                dump(json_messages, f, indent=4)
        except Exception:
            pass

        return AnswerModifier(Answer=Answer)

    except Exception as e:
        print(f"Error: {e}")
        # Clear DB history and retry
        clear_chat_history()
        try:
            with open(CHATLOG_PATH, "w") as f:
                dump([], f, indent=4)
        except Exception:
            pass
        return ChatBot(Query)

#Main program entry point.

if __name__ == "__main__":
    while True:
        user_input = input("Enter Your Question: ")
        print(ChatBot(user_input))