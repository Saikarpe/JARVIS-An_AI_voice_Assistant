# Ensure to fix imports and install: pip install python-dotenv groq duckduckgo-search

from duckduckgo_search import DDGS
from groq import Groq
from json import load, dump
import datetime
from dotenv import dotenv_values
from Backend.Database import save_message, get_chat_history, save_search

env_vars = dotenv_values(".env")
Username = env_vars.get("Username")
Assistantname = env_vars.get("Assistantname")
GroqAPIKey = env_vars.get("GroqAPIKey")
client = Groq(api_key=GroqAPIKey)

System = f"""Hello, I am {Username}, You are a very accurate and advanced AI chatbot named {Assistantname} which has real-time up-to-date information from the internet.
*** Provide Answers In a Professional Way, make sure to add full stops, commas, question marks, and use proper grammar.***
*** Just answer the question from the provided data in a professional way. ***"""

def GoogleSearch(query):
    # Using DuckDuckGo instead of Google to avoid 429 Too Many Requests and hanging
    Answer = f"The search results for '{query}' are:\n[start]\n"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            for i in results:
                Answer += f"Title: {i.get('title', '')}\nDescription: {i.get('body', '')}\n\n"
    except Exception as e:
        print(f"Search failed: {e}")
        Answer += "Failed to retrieve search results due to an error or rate limit.\n"
    Answer += "[end]"
    return Answer

def AnswerModifier(Answer):
    lines = Answer.split('\n')
    return '\n'.join([line for line in lines if line.strip()])

def Information():
    now = datetime.datetime.now()
    return (
        "Use This Real-time Information if needed:\n"
        f"Day: {now.strftime('%A')}\n"
        f"Date: {now.strftime('%d')}\n"
        f"Month: {now.strftime('%B')}\n"
        f"Year: {now.strftime('%Y')}\n"
        f"Time: {now.strftime('%H')} hours, {now.strftime('%M')} minutes, {now.strftime('%S')} seconds.\n"
    )

def RealtimeSearchEngine(prompt):
    # Get chat history from SQLite database
    messages = get_chat_history(limit=20)
    messages.append({"role": "user", "content": prompt})

    # Perform the web search
    search_results = GoogleSearch(prompt)

    SystemChatBot = [
        {"role": "system", "content": System},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello, how can I help you?"},
        {"role": "user", "content": search_results},
        {"role": "system", "content": Information()}
    ]

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=SystemChatBot + messages,
        temperature=0.7,
        max_tokens=2048,
        top_p=1,
        stream=True
    )

    Answer = ""
    for chunk in completion:
        if chunk.choices[0].delta.content:
            Answer += chunk.choices[0].delta.content

    Answer = Answer.strip().replace("</s>", "")

    # Save to SQLite database
    save_message("user", prompt)
    save_message("assistant", Answer)
    save_search(query=prompt, search_results=search_results, ai_response=Answer)

    # Also update ChatLog.json for backwards compatibility
    try:
        json_messages = get_chat_history(limit=100)
        with open("Data/ChatLog.json", "w") as f:
            dump(json_messages, f, indent=4)
    except Exception:
        pass

    return AnswerModifier(Answer)

if __name__ == "__main__":
    while True:
        prompt = input("Enter your query: ")
        print(RealtimeSearchEngine(prompt))
