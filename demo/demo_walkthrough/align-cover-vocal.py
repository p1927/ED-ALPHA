#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_AUDIO_DIR = REPO_ROOT / "public" / "demo_walkthrough" / "audio" / "cover_vocal"
SOURCE_AUDIO_DIR = REPO_ROOT / "demo_walkthrough" / "audio" / "cover_vocal"
MANIFEST_PATH = PUBLIC_AUDIO_DIR / "manifest.json"
PUBLIC_ALIGNMENT_PATH = PUBLIC_AUDIO_DIR / "alignment.json"
SOURCE_ALIGNMENT_PATH = SOURCE_AUDIO_DIR / "alignment.json"
FFPROBE = Path("/usr/local/bin/ffprobe")
GAP_SECONDS = 0.6
MIN_WORD_SECONDS = 0.04
MAX_UNMATCHED_RATIO = 0.35


@dataclass
class RawWord:
    text: str
    start: float
    end: float


def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def expected_tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def audio_duration(path: Path) -> float:
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
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def load_whisper_timestamped():
    try:
        import whisper_timestamped as whisper  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "whisper-timestamped is not installed. Create the local env with:\n"
            "  /usr/local/bin/python3 -m venv demo_walkthrough/.venv-align\n"
            "  demo_walkthrough/.venv-align/bin/python -m pip install -U pip setuptools wheel\n"
            "  demo_walkthrough/.venv-align/bin/python -m pip install whisper-timestamped torch\n"
        ) from exc
    return whisper


def transcribe_words(whisper: Any, model: Any, audio_path: Path, prompt: str) -> list[RawWord]:
    audio = whisper.load_audio(str(audio_path))
    result = whisper.transcribe(
        model,
        audio,
        language="en",
        task="transcribe",
        initial_prompt=prompt,
        temperature=0,
        beam_size=5,
        best_of=5,
        vad=False,
        detect_disfluencies=False,
        verbose=False,
    )

    words: list[RawWord] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            raw_text = str(word.get("text") or word.get("word") or "").strip()
            if not raw_text:
                continue
            start = float(word.get("start", 0.0))
            end = float(word.get("end", start))
            if end <= start:
                end = start + MIN_WORD_SECONDS
            words.append(RawWord(raw_text, start, end))
    return words


def build_char_to_word(words: list[RawWord]) -> tuple[str, list[int]]:
    chars: list[str] = []
    char_to_word: list[int] = []
    for word_index, word in enumerate(words):
        normalized = normalize_token(word.text)
        for char in normalized:
            chars.append(char)
            char_to_word.append(word_index)
    return "".join(chars), char_to_word


def expected_char_spans(tokens: list[str]) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        normalized = normalize_token(token)
        start = cursor
        chars.extend(normalized)
        cursor += len(normalized)
        spans.append((start, cursor))
    return "".join(chars), spans


def map_expected_chars_to_recognized(expected_chars: str, recognized_chars: str) -> list[int | None]:
    mapping: list[int | None] = [None] * len(expected_chars)
    matcher = difflib.SequenceMatcher(a=expected_chars, b=recognized_chars, autojunk=False)
    for tag, exp_start, exp_end, rec_start, _rec_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset, exp_index in enumerate(range(exp_start, exp_end)):
            mapping[exp_index] = rec_start + offset
    return mapping


def interpolate_missing(words: list[dict[str, Any]], duration: float) -> None:
    matched = [index for index, word in enumerate(words) if word["source"] == "whisper"]
    if not matched:
        token_duration = duration / max(len(words), 1)
        for index, word in enumerate(words):
            word["start"] = round(index * token_duration, 3)
            word["end"] = round(min(duration, (index + 1) * token_duration), 3)
            word["source"] = "interpolated_all"
        return

    def fill_range(start_index: int, end_index: int, start_time: float, end_time: float) -> None:
        count = end_index - start_index
        if count <= 0:
            return
        span = max(end_time - start_time, count * MIN_WORD_SECONDS)
        step = span / count
        for offset, index in enumerate(range(start_index, end_index)):
            words[index]["start"] = round(start_time + step * offset, 3)
            words[index]["end"] = round(min(end_time, start_time + step * (offset + 1)), 3)
            words[index]["source"] = "interpolated"

    first = matched[0]
    fill_range(0, first, 0.0, float(words[first]["start"]))

    for left, right in zip(matched, matched[1:]):
        fill_range(left + 1, right, float(words[left]["end"]), float(words[right]["start"]))

    last = matched[-1]
    fill_range(last + 1, len(words), float(words[last]["end"]), duration)

    previous_end = 0.0
    for word in words:
        start = max(float(word["start"]), previous_end)
        end = max(float(word["end"]), start + MIN_WORD_SECONDS)
        word["start"] = round(min(start, duration), 3)
        word["end"] = round(min(end, duration), 3)
        previous_end = float(word["end"])


def reconcile_words(expected_text: str, raw_words: list[RawWord], duration: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tokens = expected_tokens(expected_text)
    expected_chars, expected_spans = expected_char_spans(tokens)
    recognized_chars, recognized_char_to_word = build_char_to_word(raw_words)
    char_mapping = map_expected_chars_to_recognized(expected_chars, recognized_chars)
    aligned_words: list[dict[str, Any]] = []
    unmatched = 0

    for token, (exp_start, exp_end) in zip(tokens, expected_spans):
        recognized_word_indexes = {
            recognized_char_to_word[rec_index]
            for rec_index in (
                char_mapping[exp_index]
                for exp_index in range(exp_start, exp_end)
                if exp_index < len(char_mapping)
            )
            if rec_index is not None and rec_index < len(recognized_char_to_word)
        }
        if recognized_word_indexes:
            starts = [raw_words[index].start for index in recognized_word_indexes]
            ends = [raw_words[index].end for index in recognized_word_indexes]
            aligned_words.append(
                {
                    "text": token,
                    "start": round(max(0.0, min(starts)), 3),
                    "end": round(min(duration, max(ends)), 3),
                    "source": "whisper",
                }
            )
        else:
            unmatched += 1
            aligned_words.append({"text": token, "start": None, "end": None, "source": "missing"})

    max_unmatched = max(2, math.ceil(len(tokens) * MAX_UNMATCHED_RATIO))
    if unmatched > max_unmatched:
        raise RuntimeError(
            f"Too many unmatched words for sentence: {unmatched}/{len(tokens)}. "
            f"Expected={tokens!r}; recognized={[word.text for word in raw_words]!r}"
        )

    interpolate_missing(aligned_words, duration)
    stats = {
        "expected_words": len(tokens),
        "raw_whisper_words": len(raw_words),
        "direct_words": sum(1 for word in aligned_words if word["source"] == "whisper"),
        "interpolated_words": sum(1 for word in aligned_words if str(word["source"]).startswith("interpolated")),
    }
    return aligned_words, stats


def public_audio_file(manifest_file: str) -> Path:
    name = Path(manifest_file).name
    return PUBLIC_AUDIO_DIR / name


def build_alignment(model_name: str, limit: int | None = None) -> dict[str, Any]:
    if not FFPROBE.exists():
        raise SystemExit(f"ffprobe not found at {FFPROBE}")
    if shutil.which("ffmpeg") is None and not Path("/usr/local/bin/ffmpeg").exists():
        raise SystemExit("ffmpeg is required for Whisper audio decoding")

    os.environ.setdefault("XDG_CACHE_HOME", str(REPO_ROOT / "demo_walkthrough" / ".cache-align"))
    whisper = load_whisper_timestamped()
    model = whisper.load_model(model_name, device="cpu", download_root=str(REPO_ROOT / "demo_walkthrough" / ".cache-align" / "whisper"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    cues: list[dict[str, Any]] = []
    cursor = 0.0
    lines = manifest["lines"][:limit] if limit else manifest["lines"]
    for line in lines:
        audio_path = public_audio_file(line["file"])
        duration = round(audio_duration(audio_path), 3)
        print(f"Aligning {line['index']:02d}/{len(lines):02d}: {audio_path.name} ({duration:.3f}s)", flush=True)
        raw_words = transcribe_words(whisper, model, audio_path, line["text"])
        words, stats = reconcile_words(line["text"], raw_words, duration)
        start = round(cursor, 3)
        end = round(start + duration, 3)
        cues.append(
            {
                "index": line["index"],
                "scene_id": line["scene_id"],
                "scene_line_index": line["scene_line_index"],
                "text": line["text"],
                "audio_file": audio_path.name,
                "start": start,
                "end": end,
                "duration_seconds": duration,
                "words": words,
                "alignment_stats": stats,
            }
        )
        cursor = end + GAP_SECONDS

    duration_seconds = cues[-1]["end"] if cues else 0.0
    return {
        "version": 1,
        "aligner": "whisper-timestamped",
        "model": model_name,
        "source_manifest": str(MANIFEST_PATH.relative_to(REPO_ROOT)),
        "timestamp_capture": (
            "Word timestamps come from whisper_timestamped.transcribe(...).segments[].words[]. "
            "Each raw word start/end is relative to its MP3. Display tokens are reconciled to those "
            "raw words by normalized character matching; unmatched tokens are interpolated only when "
            "the unmatched count stays under the configured threshold."
        ),
        "gap_seconds": GAP_SECONDS,
        "duration_seconds": round(duration_seconds, 3),
        "cue_count": len(cues),
        "cues": cues,
    }


def validate_alignment(alignment: dict[str, Any]) -> None:
    cues = alignment["cues"]
    if alignment["cue_count"] != len(cues):
        raise RuntimeError("cue_count does not match cues length")
    for cue in cues:
        if not cue["words"]:
            raise RuntimeError(f"Cue {cue['index']} has no aligned words")
        if cue["end"] <= cue["start"]:
            raise RuntimeError(f"Cue {cue['index']} has invalid cue range")
        previous_end = -1.0
        for word in cue["words"]:
            if word["start"] is None or word["end"] is None:
                raise RuntimeError(f"Cue {cue['index']} has an unresolved word timestamp")
            if word["end"] <= word["start"]:
                raise RuntimeError(f"Cue {cue['index']} has a non-positive word duration")
            if word["start"] + 0.005 < previous_end:
                raise RuntimeError(f"Cue {cue['index']} word timestamps are not monotonic")
            previous_end = word["end"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Align generated cover vocal MP3s to subtitle word timestamps.")
    parser.add_argument("--model", default="tiny.en", help="Whisper model name to use for alignment.")
    parser.add_argument("--limit", type=int, default=None, help="Align only the first N cues for a quick smoke test.")
    args = parser.parse_args()

    alignment = build_alignment(args.model, args.limit)
    validate_alignment(alignment)
    PUBLIC_ALIGNMENT_PATH.write_text(json.dumps(alignment, indent=2) + "\n", encoding="utf-8")
    SOURCE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_ALIGNMENT_PATH.write_text(json.dumps(alignment, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {PUBLIC_ALIGNMENT_PATH.relative_to(REPO_ROOT)}")
    print(f"Wrote {SOURCE_ALIGNMENT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
