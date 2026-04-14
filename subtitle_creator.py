#!/usr/bin/env python3
"""
Subtitle Creator - Genera subtitulos SRT desde video MP4 usando Groq Whisper.

Uso CLI: python subtitle_creator.py video.mp4
Uso GUI: python gui.py
"""

import sys
import os
import shutil
import subprocess
import time
from pathlib import Path

from groq import Groq

import config


# ─── Key Rotation ────────────────────────────────────────────

class KeyRotator:
    """Rota API keys de Groq cuando se agota el rate limit."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("No hay API keys configuradas en GROQ_API_KEYS")
        self.keys = keys
        self.current = 0
        self.exhausted = set()

    @property
    def key(self) -> str:
        return self.keys[self.current]

    def client(self) -> Groq:
        return Groq(api_key=self.key)

    def rotate(self) -> bool:
        self.exhausted.add(self.current)
        if len(self.exhausted) >= len(self.keys):
            return False
        for i in range(len(self.keys)):
            candidate = (self.current + 1 + i) % len(self.keys)
            if candidate not in self.exhausted:
                self.current = candidate
                return True
        return False

    def call(self, fn, log, *args, max_retries=3, **kwargs):
        """Ejecuta fn(client, *args, **kwargs) con rotacion y retry automatico."""
        for attempt in range(max_retries * len(self.keys)):
            try:
                return fn(self.client(), *args, **kwargs)
            except Exception as e:
                err = str(e).lower()
                if "rate_limit" in err or "429" in err or "limit" in err:
                    log(f"    Rate limit en key #{self.current + 1}")
                    if not self.rotate():
                        self.exhausted.clear()
                        log(f"    Todas las keys agotadas. Esperando 30s...")
                        time.sleep(30)
                elif "413" in err or "too large" in err:
                    raise
                else:
                    raise
        raise RuntimeError("Se agotaron todos los reintentos con todas las keys")


# ─── Audio Extraction ────────────────────────────────────────

def get_audio_duration(file_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", file_path],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def extract_audio_ogg(video_path: str, output_path: str,
                      start_sec: float = 0, duration_sec: float = None) -> None:
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "libopus",
        "-ar", str(config.SAMPLE_RATE), "-ac", "1", "-b:a", "32k",
        "-ss", str(start_sec),
    ]
    if duration_sec:
        cmd.extend(["-t", str(duration_sec)])
    cmd.extend(["-y", output_path])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Error ffmpeg: {result.stderr}")


def extract_audio(video_path: str, temp_dir: str, log=print) -> list[dict]:
    log(f"[1/4] Extrayendo audio de {Path(video_path).name}...")
    duration = get_audio_duration(video_path)

    full_path = os.path.join(temp_dir, "full_audio.ogg")
    extract_audio_ogg(video_path, full_path)
    size_mb = os.path.getsize(full_path) / (1024 * 1024)

    if size_mb < 24:
        log(f"    Audio completo: {size_mb:.1f}MB ({duration/60:.1f} min) - sin chunking")
        return [{"path": full_path, "offset": 0, "duration": duration, "size_mb": size_mb}]

    log(f"    Audio {size_mb:.1f}MB > 24MB, dividiendo en chunks...")
    os.remove(full_path)
    chunk_secs = config.AUDIO_CHUNK_MINUTES * 60
    chunks = []
    offset = 0
    i = 0
    while offset < duration:
        chunk_path = os.path.join(temp_dir, f"chunk_{i:03d}.ogg")
        chunk_dur = min(chunk_secs, duration - offset)
        extract_audio_ogg(video_path, chunk_path, start_sec=offset, duration_sec=chunk_dur)
        size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
        chunks.append({"path": chunk_path, "offset": offset, "duration": chunk_dur, "size_mb": size_mb})
        log(f"    Chunk {i + 1}: {offset/60:.1f}-{(offset + chunk_dur)/60:.1f} min ({size_mb:.1f}MB)")
        offset += chunk_secs
        i += 1
    log(f"    {len(chunks)} chunks extraidos ({duration/60:.1f} min total)")
    return chunks


# ─── Transcription ───────────────────────────────────────────

def _whisper_transcribe(client: Groq, chunk_path: str, source_lang: str) -> object:
    with open(chunk_path, "rb") as f:
        return client.audio.transcriptions.create(
            file=(os.path.basename(chunk_path), f.read()),
            model=config.WHISPER_MODEL,
            response_format="verbose_json",
            language=source_lang,
            timestamp_granularities=["word", "segment"],
        )


def transcribe_chunks(chunks: list[dict], rotator: KeyRotator,
                      source_lang: str = "en", log=print) -> list[dict]:
    log(f"[2/4] Transcribiendo con Whisper ({config.WHISPER_MODEL})...")
    all_words = []

    for i, chunk in enumerate(chunks):
        log(f"    Chunk {i + 1}/{len(chunks)}...")
        start = time.time()
        response = rotator.call(_whisper_transcribe, log, chunk["path"], source_lang)

        words = []
        raw_words = getattr(response, "words", None) or []
        for w in raw_words:
            if isinstance(w, dict):
                word, ws, we = w.get("word", ""), w.get("start", 0), w.get("end", 0)
            else:
                word, ws, we = w.word, w.start, w.end
            words.append({"word": word.strip(), "start": ws + chunk["offset"], "end": we + chunk["offset"]})

        elapsed = time.time() - start
        log(f"      {len(words)} palabras ({elapsed:.1f}s)")
        all_words.extend(words)

    log(f"    Transcripcion completa: {len(all_words)} palabras")
    return all_words


# ─── Subtitle Segmentation ──────────────────────────────────

def group_words_into_subtitles(words: list[dict], log=print) -> list[dict]:
    if not words:
        return []

    NOISE_WORDS = {"the", "a", "an", "uh", "um", "hmm", "huh", "oh", "ah"}

    subtitles = []
    current_words = []
    current_start = None

    for word in words:
        if not current_words:
            current_start = word["start"]
            current_words.append(word)
            continue

        duration = word["end"] - current_start
        gap = word["start"] - current_words[-1]["end"]
        word_count = len(current_words)

        should_split = (
            gap >= config.PAUSE_THRESHOLD or
            word_count >= config.MAX_WORDS_PER_SUBTITLE or
            duration >= config.MAX_SUBTITLE_DURATION
        )

        if should_split:
            text = " ".join(w["word"] for w in current_words)
            meaningful = [w for w in current_words if w["word"].lower() not in NOISE_WORDS]
            if meaningful:
                subtitles.append({
                    "index": len(subtitles) + 1,
                    "start": current_start,
                    "end": current_words[-1]["end"],
                    "text": text,
                })
            current_words = [word]
            current_start = word["start"]
        else:
            current_words.append(word)

    if current_words:
        text = " ".join(w["word"] for w in current_words)
        meaningful = [w for w in current_words if w["word"].lower() not in NOISE_WORDS]
        if meaningful:
            subtitles.append({
                "index": len(subtitles) + 1,
                "start": current_start,
                "end": current_words[-1]["end"],
                "text": text,
            })

    # Filtrar basura de Whisper
    clean = []
    removed = 0
    VALID_SINGLE = {"no", "yes", "stop", "go", "run", "help", "wait", "please",
                    "hello", "hey", "now", "fire", "look", "listen", "enough",
                    "what", "why", "really", "never", "always", "sorry", "thanks"}

    for sub in subtitles:
        text = sub["text"]
        duration = sub["end"] - sub["start"]
        stripped = text.strip(".,!?¡¿ '\"")
        wds = stripped.split()

        if duration < 0.2:
            removed += 1; continue
        has_non_latin = any(ord(c) > 0x024F and c not in "¡¿áéíóúñüÁÉÍÓÚÑÜ—–''\"\""
                            for c in text)
        if has_non_latin:
            removed += 1; continue
        if len(wds) == 1 and stripped.lower() not in VALID_SINGLE:
            removed += 1; continue
        if len(wds) == 2 and all(len(w.strip(".,!?")) <= 3 for w in wds):
            removed += 1; continue
        if stripped.lower() in ("transcription", "visit", "subscribe", "thank you for watching"):
            removed += 1; continue

        sub["index"] = len(clean) + 1
        clean.append(sub)

    if removed:
        log(f"    Filtrados {removed} subtitulos basura de Whisper")
    log(f"[3/4] Subtitulos segmentados: {len(clean)} bloques")
    return clean


# ─── SRT Writing ─────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(subtitles: list[dict], output_path: str, log=print) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        for sub in subtitles:
            f.write(f"{sub['index']}\n")
            f.write(f"{format_timestamp(sub['start'])} --> {format_timestamp(sub['end'])}\n")
            f.write(f"{sub['text']}\n\n")
    log(f"    SRT guardado: {output_path}")


# ─── Translation ─────────────────────────────────────────────

LANG_NAMES = config.LANGUAGES


def _llm_translate(client: Groq, prompt: str) -> str:
    response = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()


def _is_still_english(original: str, translated: str) -> bool:
    if original.strip().lower() == translated.strip().lower():
        return True
    en_markers = {"the", "is", "are", "was", "were", "have", "has", "had",
                  "will", "would", "could", "should", "can", "don't", "didn't",
                  "i'm", "you're", "we're", "they're", "isn't", "aren't",
                  "that", "this", "with", "from", "what", "where", "when",
                  "how", "why", "who", "which", "there", "their", "your"}
    words = translated.lower().split()
    if len(words) < 3:
        return False
    en_count = sum(1 for w in words if w.strip(".,!?'\"") in en_markers)
    return en_count / len(words) >= 0.3


def _translate_batch(batch: list[dict], lang_name: str, rotator: KeyRotator, log=print) -> dict:
    lines = [f"{sub['index']}|{sub['text']}" for sub in batch]
    text_block = "\n".join(lines)

    prompt = (
        f"You are a professional subtitle translator. Translate each line to {lang_name}.\n\n"
        f"STRICT RULES:\n"
        f"- Each line has format: number|text\n"
        f"- Translate ONLY the text after the pipe, keep the number unchanged\n"
        f"- Output EXACTLY {len(batch)} lines, one per input line\n"
        f"- Do NOT merge, split, move, or reorder text between lines\n"
        f"- Each translated line must ONLY contain the translation of that specific line\n"
        f"- If a line has a partial sentence, translate that partial sentence only\n"
        f"- Keep the meaning and context, adapt expressions naturally\n"
        f"- ALL output text MUST be in {lang_name}, never leave English words (except proper nouns)\n"
        f"- Output format: number|translated text\n"
        f"- No explanations, no extra text\n\n"
        f"{text_block}"
    )

    response_text = rotator.call(_llm_translate, log, prompt)

    translation_map = {}
    for line in response_text.split("\n"):
        line = line.strip()
        if "|" in line:
            parts = line.split("|", 1)
            try:
                idx = int(parts[0].strip())
                translation_map[idx] = parts[1].strip()
            except ValueError:
                continue
    return translation_map


def translate_subtitles(subtitles: list[dict], target_lang: str,
                        rotator: KeyRotator, log=print) -> list[dict]:
    lang_name = LANG_NAMES.get(target_lang, target_lang)
    log(f"[4/4] Traduciendo a '{lang_name}' con {config.GROQ_MODEL}...")

    batch_size = 20
    translated = []

    for batch_start in range(0, len(subtitles), batch_size):
        batch = subtitles[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(subtitles) + batch_size - 1) // batch_size
        log(f"    Bloque {batch_num}/{total_batches}...")

        translation_map = _translate_batch(batch, lang_name, rotator, log)

        for sub in batch:
            translated_sub = sub.copy()
            if sub["index"] in translation_map:
                translated_sub["text"] = translation_map[sub["index"]]
            translated.append(translated_sub)

        log(f"      {len(translation_map)}/{len(batch)} traducidos")

    # Verificacion: reintentar lineas en ingles
    retry_subs = []
    for i, (orig, trans) in enumerate(zip(subtitles, translated)):
        if _is_still_english(orig["text"], trans["text"]):
            retry_subs.append((i, orig))

    if retry_subs:
        log(f"    Verificacion: {len(retry_subs)} lineas en ingles, reintentando...")
        for retry_start in range(0, len(retry_subs), batch_size):
            retry_batch_items = retry_subs[retry_start:retry_start + batch_size]
            batch = [item[1] for item in retry_batch_items]
            translation_map = _translate_batch(batch, lang_name, rotator, log)
            fixed = 0
            for idx_in_list, orig in retry_batch_items:
                if orig["index"] in translation_map:
                    new_text = translation_map[orig["index"]]
                    if not _is_still_english(orig["text"], new_text):
                        translated[idx_in_list]["text"] = new_text
                        fixed += 1
            log(f"      {fixed} corregidos")

    log(f"    Traduccion '{lang_name}' completa: {len(translated)} subtitulos")
    return translated


# ─── Pipeline ────────────────────────────────────────────────

def process_video(video_path: str, source_lang: str = "en",
                  target_langs: list[str] = None, api_keys: list[str] = None,
                  log=print) -> list[str]:
    """Pipeline completo. Retorna lista de archivos SRT generados."""
    keys = api_keys or config.GROQ_API_KEYS
    rotator = KeyRotator(keys)

    video_stem = Path(video_path).stem
    video_dir = Path(video_path).parent
    temp_dir = str(video_dir / f".{video_stem}_temp")
    os.makedirs(temp_dir, exist_ok=True)
    generated = []

    try:
        total_start = time.time()

        # 1. Extraer audio
        chunks = extract_audio(video_path, temp_dir, log)

        # 2. Transcribir
        words = transcribe_chunks(chunks, rotator, source_lang, log)
        if not words:
            raise RuntimeError("No se detectaron palabras en el audio.")

        # 3. Segmentar y escribir SRT original
        subtitles = group_words_into_subtitles(words, log)
        srt_original = str(video_dir / f"{video_stem}.srt")
        write_srt(subtitles, srt_original, log)
        generated.append(srt_original)

        # 4. Traducir a cada idioma
        if target_langs:
            for lang in target_langs:
                translated = translate_subtitles(subtitles, lang, rotator, log)
                if translated:
                    srt_path = str(video_dir / f"{video_stem}_{lang}.srt")
                    write_srt(translated, srt_path, log)
                    generated.append(srt_path)

        elapsed = time.time() - total_start
        log(f"\nCompletado en {elapsed:.1f}s!")
        for f in generated:
            log(f"  {Path(f).name}")

    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    return generated


# ─── CLI ─────────────────────────────────────────────────────

def main():
    VIDEOS_DIR = Path(__file__).parent / "videos"
    VIDEOS_DIR.mkdir(exist_ok=True)

    if len(sys.argv) < 2:
        mp4_files = list(VIDEOS_DIR.glob("*.mp4"))
        if not mp4_files:
            print("Uso: python subtitle_creator.py <video.mp4>")
            print(f"  O coloca un archivo .mp4 en la carpeta: {VIDEOS_DIR}")
            sys.exit(1)
        video_path = str(mp4_files[0])
        print(f"Video encontrado en videos/: {mp4_files[0].name}")
    else:
        video_path = sys.argv[1]
        if not os.path.exists(video_path):
            candidate = VIDEOS_DIR / video_path
            if candidate.exists():
                video_path = str(candidate)
            else:
                print(f"Error: No se encontro el archivo '{video_path}'")
                sys.exit(1)

    process_video(
        video_path=video_path,
        source_lang=config.SOURCE_LANGUAGE,
        target_langs=[config.TARGET_LANGUAGE],
    )


if __name__ == "__main__":
    main()
