from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .ollama import OllamaClient


SYSTEM_PROMPT = """你是嚴謹的繁體中文新聞影片編輯。分析逐字稿，選出兩個適合顯示右側資訊圖卡的時刻。
只能使用逐字稿明確出現的事實，不得補充、推測或捏造。圖卡應整理重要地點、數字或事件重點，避免重複完整字幕。
只輸出 JSON，格式為：
{"visuals":[{"start":秒數,"duration":3到5,"title":"短標題","lines":["短句1","短句2"],"source_segment_ids":[整數],"reason":"原因"}]}
標題最多14個中文字，每個短句最多20個中文字。圖卡之間不可重疊。"""


def plan_visuals(client: OllamaClient, segments: list[dict[str, Any]], video_duration: float) -> list[dict[str, Any]]:
    compact = json.dumps(segments, ensure_ascii=False)
    response = client.chat_json(SYSTEM_PROMPT, f"影片長度：{video_duration:.1f}秒\n逐字稿：{compact}")
    by_id = {segment["id"]: segment for segment in segments}
    validated = []
    for item in response.get("visuals", [])[:2]:
        ids = [int(value) for value in item.get("source_segment_ids", []) if int(value) in by_id]
        if not ids:
            continue
        start = float(item["start"])
        duration = min(5.0, max(3.0, float(item.get("duration", 4))))
        if start < 0 or start + duration > video_duration:
            continue
        source_text = " ".join(by_id[value]["text"] for value in ids)
        validated.append({
            "start": round(start, 3),
            "end": round(start + duration, 3),
            "duration": duration,
            "type": "card",
            "title": str(item["title"])[:20],
            "lines": [str(line)[:30] for line in item.get("lines", [])[:3]],
            "source_segment_ids": ids,
            "source_text": source_text,
            "reason": str(item.get("reason", "")),
        })
    if not validated:
        raise RuntimeError("Qwen did not return any valid visual cards")
    return validated


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def render_cards(visuals: list[dict[str, Any]], output_dir: Path, font_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_card in output_dir.glob("visual_*.png"):
        old_card.unlink()
    for index, visual in enumerate(visuals, start=1):
        canvas = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        box = (1200, 165, 1845, 730)
        draw.rounded_rectangle(box, radius=34, fill=(9, 30, 48, 235), outline=(64, 190, 230, 255), width=4)
        draw.rounded_rectangle((1240, 205, 1425, 252), radius=18, fill=(17, 150, 190, 255))
        draw.text((1265, 211), "新聞重點", font=_font(font_path, 25), fill="white")
        draw.text((1240, 295), visual["title"], font=_font(font_path, 46), fill=(255, 238, 165, 255))
        y = 400
        body_font = _font(font_path, 30)
        for line in visual["lines"]:
            wrapped = textwrap.wrap(line, width=19) or [line]
            for part_index, part in enumerate(wrapped):
                prefix = "• " if part_index == 0 else "  "
                draw.text((1240, y), prefix + part, font=body_font, fill="white")
                y += 52
            y += 18
        visual["file"] = str((output_dir / f"visual_{index:02}.png").resolve())
        canvas.save(visual["file"])
