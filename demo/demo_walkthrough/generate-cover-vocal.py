#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = REPO_ROOT / "demo_walkthrough" / "data" / "scripts.json"
OUTPUT_DIR = REPO_ROOT / "demo_walkthrough" / "audio" / "cover_vocal"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
MODEL = "gemini-2.5-flash-preview-tts"
VOICE = "Zephyr"
STYLE_PROMPT = "Energetic, upbeat podcast host tone. Friendly and engaging, clear enunciation."
FFMPEG = Path("/usr/local/bin/ffmpeg")
FFPROBE = Path("/usr/local/bin/ffprobe")
INPUT_USD_PER_1M_TEXT_TOKENS = 0.50
OUTPUT_USD_PER_1M_AUDIO_TOKENS = 10.00
ESTIMATED_AUDIO_TOKENS_PER_SECOND = 32
ESTIMATED_WORDS_PER_MINUTE = 155
MAX_RETRIES = 4
DEFAULT_REQUEST_DELAY_SECONDS = 7.0


@dataclass(frozen=True)
class Line:
    index: int
    scene_id: str
    scene_line_index: int
    text: str

    @property
    def stem(self) -> str:
        scene = re.sub(r"[^a-z0-9]+", "-", self.scene_id.lower()).strip("-")
        return f"{self.index:03d}_{scene}_{self.scene_line_index:02d}"

    @property
    def tts_text(self) -> str:
        return self.text.replace("ED-ALPHA", "E D Alpha")


def extract_subtitle_lines(source: Path) -> list[Line]:
    data = json.loads(source.read_text(encoding="utf-8"))
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise RuntimeError(f"No scenes array found in {source}")

    lines: list[Line] = []

    for scene in scenes:
        scene_id = scene.get("id")
        subtitles = scene.get("subtitles")
        if not isinstance(scene_id, str):
            raise RuntimeError(f"Scene is missing a string id in {source}")
        if not isinstance(subtitles, list):
            raise RuntimeError(f"Scene {scene_id} is missing a subtitles array in {source}")

        for scene_line_index, subtitle in enumerate(subtitles, start=1):
            if not isinstance(subtitle, str):
                raise RuntimeError(f"Scene {scene_id} subtitle {scene_line_index} is not a string")
            lines.append(
                Line(
                    index=len(lines) + 1,
                    scene_id=scene_id,
                    scene_line_index=scene_line_index,
                    text=subtitle,
                )
            )

    if not lines:
        raise RuntimeError(f"No subtitle lines found in {source}")
    return lines


def estimate_input_tokens(text: str) -> int:
    prompt = render_prompt(text)
    return max(1, (len(prompt) + 3) // 4)


def estimate_duration_seconds(text: str) -> float:
    words = len(re.findall(r"\S+", text))
    return max(0.5, words / ESTIMATED_WORDS_PER_MINUTE * 60.0)


def estimate_cost(lines: list[Line]) -> dict[str, float | int]:
    input_tokens = sum(estimate_input_tokens(line.tts_text) for line in lines)
    seconds = sum(estimate_duration_seconds(line.tts_text) for line in lines)
    output_tokens = round(seconds * ESTIMATED_AUDIO_TOKENS_PER_SECOND)
    input_usd = input_tokens / 1_000_000.0 * INPUT_USD_PER_1M_TEXT_TOKENS
    output_usd = output_tokens / 1_000_000.0 * OUTPUT_USD_PER_1M_AUDIO_TOKENS
    return {
        "line_count": len(lines),
        "estimated_input_tokens": input_tokens,
        "estimated_output_audio_tokens": output_tokens,
        "estimated_seconds": seconds,
        "estimated_usd": input_usd + output_usd,
    }


def estimate_line_cost(tts_text: str, duration_seconds: float | None = None) -> dict[str, float | int]:
    input_tokens = estimate_input_tokens(tts_text)
    seconds = duration_seconds if duration_seconds is not None else estimate_duration_seconds(tts_text)
    output_tokens = round(seconds * ESTIMATED_AUDIO_TOKENS_PER_SECOND)
    input_usd = input_tokens / 1_000_000.0 * INPUT_USD_PER_1M_TEXT_TOKENS
    output_usd = output_tokens / 1_000_000.0 * OUTPUT_USD_PER_1M_AUDIO_TOKENS
    return {
        "estimated_input_tokens": input_tokens,
        "estimated_output_audio_tokens": output_tokens,
        "estimated_cost_usd": input_usd + output_usd,
    }


def render_prompt(text: str) -> str:
    return f"{STYLE_PROMPT}\n\ntext to read: {text}"


def load_api_key() -> str:
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to .env before generation.")
    return api_key


def extract_audio_data(response) -> tuple[bytes, int]:
    sample_rate = 24000
    candidates = response.candidates or []
    for candidate in candidates:
        parts = candidate.content.parts if candidate.content and candidate.content.parts else []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if not inline_data:
                continue
            mime_type = inline_data.mime_type or ""
            rate_match = re.search(r"rate=(\d+)", mime_type)
            if rate_match:
                sample_rate = int(rate_match.group(1))
            data = inline_data.data
            if isinstance(data, str):
                return base64.b64decode(data), sample_rate
            return data, sample_rate
    raise RuntimeError("No audio data returned by Gemini TTS")


def write_wav(path: Path, audio_data: bytes, sample_rate: int) -> float:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data)

    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes() / float(wav_file.getframerate())


def convert_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    if not FFMPEG.exists():
        raise RuntimeError(f"ffmpeg not found at {FFMPEG}")
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-i",
            str(wav_path),
            "-b:a",
            "64k",
            str(mp3_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def probe_duration(path: Path) -> float | None:
    if FFPROBE.exists():
        result = subprocess.run(
            [
                str(FFPROBE),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                return round(float(result.stdout.strip()), 3)
            except ValueError:
                pass

    result = subprocess.run([str(FFMPEG), "-i", str(path)], capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def usage_counts(response) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return None, None
    input_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)
    return input_tokens, output_tokens


def cost_from_usage(input_tokens: int | None, output_tokens: int | None) -> float | None:
    if input_tokens is None and output_tokens is None:
        return None
    cost = 0.0
    if input_tokens is not None:
        cost += input_tokens / 1_000_000.0 * INPUT_USD_PER_1M_TEXT_TOKENS
    if output_tokens is not None:
        cost += output_tokens / 1_000_000.0 * OUTPUT_USD_PER_1M_AUDIO_TOKENS
    return cost


def retry_delay_seconds(error: Exception, fallback: float) -> float:
    text = str(error)
    retry_delay_match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", text)
    if retry_delay_match:
        return max(fallback, float(retry_delay_match.group(1)) + 2.0)

    retry_in_match = re.search(r"retry in (\d+(?:\.\d+)?)s", text, re.I)
    if retry_in_match:
        return max(fallback, float(retry_in_match.group(1)) + 2.0)

    return fallback


def synthesize_line(client: genai.Client, line: Line, force: bool) -> dict:
    mp3_path = OUTPUT_DIR / f"{line.stem}.mp3"
    wav_path = OUTPUT_DIR / f"{line.stem}.wav"

    if mp3_path.exists() and not force:
        duration_seconds = probe_duration(mp3_path)
        return {
            "index": line.index,
            "scene_id": line.scene_id,
            "scene_line_index": line.scene_line_index,
            "text": line.text,
            "tts_text": line.tts_text,
            "model": MODEL,
            "voice": VOICE,
            "file": str(mp3_path.relative_to(REPO_ROOT)),
            "duration_seconds": duration_seconds,
            "input_tokens": None,
            "output_audio_tokens": None,
            "cost_usd": None,
            **estimate_line_cost(line.tts_text, duration_seconds),
            "skipped_existing": True,
        }

    prompt = render_prompt(line.tts_text)
    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
        )
    )

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=speech_config,
                ),
            )
            audio_data, sample_rate = extract_audio_data(response)
            duration_seconds = write_wav(wav_path, audio_data, sample_rate)
            convert_to_mp3(wav_path, mp3_path)
            wav_path.unlink(missing_ok=True)
            input_tokens, output_tokens = usage_counts(response)
            return {
                "index": line.index,
                "scene_id": line.scene_id,
                "scene_line_index": line.scene_line_index,
                "text": line.text,
                "tts_text": line.tts_text,
                "model": MODEL,
                "voice": VOICE,
                "file": str(mp3_path.relative_to(REPO_ROOT)),
                "duration_seconds": round(duration_seconds, 3),
                "input_tokens": input_tokens,
                "output_audio_tokens": output_tokens,
                "cost_usd": cost_from_usage(input_tokens, output_tokens),
                **estimate_line_cost(line.tts_text, duration_seconds),
                "skipped_existing": False,
            }
        except Exception as error:
            last_error = error
            wav_path.unlink(missing_ok=True)
            if attempt < MAX_RETRIES:
                delay = retry_delay_seconds(error, 3.0 * (attempt + 1))
                print(f"  retrying after {delay:.1f}s: {error}", flush=True)
                time.sleep(delay)

    raise RuntimeError(f"Failed to synthesize line {line.index}: {last_error}") from last_error


def write_manifest(lines: list[Line], entries: list[dict]) -> None:
    recorded_cost = sum(entry["cost_usd"] or 0.0 for entry in entries)
    estimated_cost = sum(entry["estimated_cost_usd"] or 0.0 for entry in entries)
    total_duration = sum(entry["duration_seconds"] or 0.0 for entry in entries)
    manifest = {
        "source": str(SOURCE_PATH.relative_to(REPO_ROOT)),
        "model": MODEL,
        "voice": VOICE,
        "style_prompt": STYLE_PROMPT,
        "line_count": len(lines),
        "generated_count": sum(1 for entry in entries if not entry["skipped_existing"]),
        "skipped_existing_count": sum(1 for entry in entries if entry["skipped_existing"]),
        "total_duration_seconds": round(total_duration, 3),
        "estimated_total_cost_usd": estimated_cost,
        "recorded_usage_cost_usd": recorded_cost,
        "recorded_usage_note": "Recorded usage includes only requests made in the current completed run; skipped existing files use estimates.",
        "pricing": {
            "input_usd_per_1m_text_tokens": INPUT_USD_PER_1M_TEXT_TOKENS,
            "output_usd_per_1m_audio_tokens": OUTPUT_USD_PER_1M_AUDIO_TOKENS,
        },
        "lines": entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def ensure_environment() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not FFMPEG.exists():
        raise RuntimeError(f"Expected ffmpeg at {FFMPEG}")
    if shutil.which(str(FFMPEG)) is None and not FFMPEG.exists():
        raise RuntimeError("ffmpeg is unavailable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate line-by-line Gemini TTS MP3 cover vocal files.")
    parser.add_argument("--dry-run", action="store_true", help="Extract lines and estimate cost without API calls.")
    parser.add_argument("--force", action="store_true", help="Regenerate MP3 files even when they already exist.")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated one-based line indexes to regenerate or inspect, for example: 1,9,35.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help="Seconds to wait after each generated API call to stay within Gemini per-minute quotas.",
    )
    args = parser.parse_args()

    lines = extract_subtitle_lines(SOURCE_PATH)
    selected_indexes = {
        int(value.strip())
        for value in args.only.split(",")
        if value.strip()
    }
    selected_lines = [line for line in lines if not selected_indexes or line.index in selected_indexes]
    if selected_indexes and len(selected_lines) != len(selected_indexes):
        found = {line.index for line in selected_lines}
        missing = sorted(selected_indexes - found)
        raise RuntimeError(f"Requested --only line indexes not found: {missing}")
    estimate = estimate_cost(lines)

    print(f"Found {len(lines)} subtitle lines in {SOURCE_PATH.relative_to(REPO_ROOT)}")
    print(
        "Estimated cost: "
        f"${estimate['estimated_usd']:.4f} "
        f"({estimate['estimated_seconds']:.1f}s, "
        f"{estimate['estimated_input_tokens']} input tokens, "
        f"{estimate['estimated_output_audio_tokens']} output audio tokens)"
    )

    if args.dry_run:
        for line in selected_lines:
            suffix = f" | TTS: {line.tts_text}" if line.tts_text != line.text else ""
            print(f"{line.stem}: {line.text}{suffix}")
        return 0

    ensure_environment()
    api_key = load_api_key()
    client = genai.Client(api_key=api_key)

    entries = []
    for line in lines:
        print(f"[{line.index:02d}/{len(lines):02d}] {line.stem}", flush=True)
        entry = synthesize_line(client, line, force=args.force or line.index in selected_indexes)
        entries.append(entry)
        if not entry["skipped_existing"] and line.index < len(lines) and args.request_delay > 0:
            time.sleep(args.request_delay)

    write_manifest(lines, entries)
    print(f"Wrote {len(entries)} manifest entries to {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
