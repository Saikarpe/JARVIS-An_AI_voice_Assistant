# 🤖 Jarvis – Your Personal AI Assistant

Jarvis is a powerful Python-based AI assistant that combines the capabilities of **Natural Language Processing (NLP)**, **Machine Learning (ML)**, and **Real-Time API Integration** to create an interactive voice- and text-enabled virtual assistant. Designed with a sleek UI and multithreaded performance, Jarvis can talk, listen, see, and assist you in real time.

---

## 🚀 Features

- 🧠 **Real-Time Chatbot** – Communicates using Cohere’s NLP model with context-aware responses.
- 🎙️ **Speech Recognition** – Listens and understands your voice commands using `SpeechRecognition`.
- 🔊 **Text-to-Speech (TTS)** – Responds via Azure Neural voices like `en-IN-PrabhatNeural`.
- 🖼️ **Image Generation** – Uses Stability AI for generating images from prompts.
- 🎧 **Audio Visualizer** – Glowing spherical visualizer reacts to real-time microphone input using `VisPy`.
- 🌐 **Web Automation** – Opens websites, searches Google/YouTube, and fetches data via APIs.
- 🔗 **API Integration** – Integrated with Grok AI, YouTube API, and browser utilities.
- 🎛️ **Multithreading** – Smooth, non-blocking user experience with background threads.
- 🧩 **Modular Design** – Easily extend Jarvis with new modules (e.g., calendar, weather, etc.).
- 🖥️ **Modern UI** – Built with PyQt5 for an interactive and user-friendly interface.

---

## 🛠️ Tech Stack

| Category             | Technologies Used                                                |
|----------------------|------------------------------------------------------------------|
| **Programming**       | Python 3.x                                                      |
| **UI Framework**      | PyQt5                                                           |
| **TTS & Speech**      | Azure TTS (`en-IN-PrabhatNeural`), SpeechRecognition, pyttsx3  |
| **NLP / ML**          | Cohere API, custom ML logic                                     |
| **Image Generation**  | Stability AI                                                    |
| **Visualization**     | VisPy, OpenGL, Three.js (for reference)                         |
| **Audio Processing**  | sounddevice, numpy, scipy                                       |
| **API Integration**   | YouTube Data API, Browser control, Grok AI                      |

---

## 🧪 Example Commands

```bash
Jarvis, open YouTube
Jarvis, show me a cat image
Jarvis, what is the weather today?
Jarvis, play music
Jarvis, generate an image of a futuristic city
