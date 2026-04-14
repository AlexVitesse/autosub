import os
from dotenv import load_dotenv

load_dotenv()

# API keys (separadas por coma para rotacion)
GROQ_API_KEYS = [k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()]
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3-turbo")
SOURCE_LANGUAGE = os.getenv("SOURCE_LANGUAGE", "en")
TARGET_LANGUAGE = os.getenv("TARGET_LANGUAGE", "es")

# Audio extraction
SAMPLE_RATE = 16000
AUDIO_CHUNK_MINUTES = 10

# Subtitle segmentation
MAX_WORDS_PER_SUBTITLE = 10
MAX_SUBTITLE_DURATION = 5.0  # seconds
PAUSE_THRESHOLD = 0.5  # seconds of silence to split subtitles

# Idiomas disponibles (codigo: nombre)
LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ru": "Russian", "ar": "Arabic", "hi": "Hindi",
    "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "sv": "Swedish",
}
