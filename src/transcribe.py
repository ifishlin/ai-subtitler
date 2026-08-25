from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
from opencc import OpenCC

from .ollama import OllamaClient
from .utils import timestamp


def transcribe(
    audio: Path, model_name: str, context: str = "", sensitive: bool = False
) -> tuple[list[dict[str, Any]], str]:
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    raw_segments, info = model.transcribe(
        str(audio),
        beam_size=5,
        vad_filter=not sensitive,
        condition_on_previous_text=not sensitive,
        no_speech_threshold=0.3 if sensitive else 0.6,
        log_prob_threshold=-1.5 if sensitive else -1.0,
        initial_prompt=context[:1200] or None,
    )
    converter = OpenCC("s2twp")
    segments = []
    for index, segment in enumerate(raw_segments, start=1):
        text = converter.convert(segment.text.strip())
        if text:
            segments.append({"id": index, "start": segment.start, "end": segment.end, "text": text})
    return segments, info.language


CORRECTION_PROMPT = """你是繁體中文新聞字幕校對員。請校正語音辨識逐字稿中的同音錯字、地名、道路名、機構名與標點。
只能根據原句和上下文校正，不得增加原本沒有說出的資訊。保留每一段的 id、start、end，不得合併、刪除或調整時間。
只輸出 JSON：{"segments":[{"id":1,"start":0.0,"end":1.0,"text":"校正文字"}]}"""


def correct_with_qwen(
    client: OllamaClient,
    segments: list[dict[str, Any]],
    context: str = "",
) -> list[dict[str, Any]]:
    converter = OpenCC("s2twp")
    prefix = (
        "公開影片標題與說明可用來確認專有名詞，但不可用來增加旁白沒有說的內容：\n"
        + context
        + "\n逐字稿批次：\n"
    )

    def correct_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = client.chat_json(
            CORRECTION_PROMPT,
            prefix + json.dumps({"segments": batch}, ensure_ascii=False),
        )
        corrected = response.get("segments", [])
        expected_ids = [item["id"] for item in batch]
        returned_ids = [
            int(item["id"])
            for item in corrected
            if isinstance(item, dict) and "id" in item
        ]
        if len(corrected) != len(batch) or sorted(returned_ids) != sorted(expected_ids):
            if len(batch) > 1:
                middle = len(batch) // 2
                return correct_batch(batch[:middle]) + correct_batch(batch[middle:])
            return [{**batch[0], "text": converter.convert(batch[0]["text"])}]
        by_id = {int(item["id"]): item for item in corrected}
        corrected_batch = []
        for original in batch:
            text = str(by_id[original["id"]].get("text", "")).strip() or original["text"]
            corrected_batch.append({**original, "text": converter.convert(text)})
        return corrected_batch

    result = []
    for offset in range(0, len(segments), 8):
        result.extend(correct_batch(segments[offset:offset + 8]))
    return result


def save_transcript(segments: list[dict[str, Any]], txt_path: Path, srt_path: Path) -> None:
    txt_path.write_text(
        "\n".join(f"[{timestamp(s['start'])} --> {timestamp(s['end'])}] {s['text']}" for s in segments) + "\n",
        encoding="utf-8",
    )
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{timestamp(segment['start'], True)} --> {timestamp(segment['end'], True)}\n{segment['text']}"
        )
    srt_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def merge_extra_segments(
    segments: list[dict[str, Any]], sidecar_path: Path
) -> list[dict[str, Any]]:
    """Merge reviewed captions for speech that Whisper missed (e.g. street interviews)."""
    if not sidecar_path.is_file():
        return segments
    extra = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(extra, list):
        raise ValueError(f"Expected a JSON list in {sidecar_path}")
    merged = [{**item} for item in segments]
    for item in extra:
        start, end = float(item["start"]), float(item["end"])
        text = str(item["text"]).strip()
        if text and end > start:
            merged.append({"id": 0, "start": start, "end": end, "text": text})
    merged.sort(key=lambda item: (item["start"], item["end"]))
    return [{**item, "id": index} for index, item in enumerate(merged, start=1)]
