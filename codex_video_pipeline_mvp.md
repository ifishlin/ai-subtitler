# Codex Task: MVP Video Pipeline

Use this video as the test input:

```text
https://www.youtube.com/watch?v=OfMExgr_vzY
```

## Goal

Build a local Python pipeline:

```text
YouTube video
→ download with yt-dlp
→ transcribe with Whisper
→ translate subtitles to Traditional Chinese
→ burn Chinese subtitles into video
→ use AI to identify 1–2 places where an explanatory image would help
→ generate an AI image for each selected point
→ insert the images for ~3 seconds while keeping the original audio
→ export final.mp4
```

## Run

For this MVP, hard-code the test URL above in the configuration or code.

Expected command:

```bash
python produce.py
```

## Requirements

1. Download the source video using `yt-dlp`.
2. Extract the audio.
3. Transcribe the English speech using local `faster-whisper` or `whisper`.
4. Generate timestamps for the transcript.
5. Translate the transcript into Traditional Chinese subtitles.
6. Save the Chinese subtitles as SRT.
7. Burn the Chinese subtitles into the video using FFmpeg.
8. Analyze the transcript and identify 1–2 moments where an explanatory image would improve understanding.
9. For each selected moment, generate:
   - start time
   - end time
   - reason
   - image prompt
10. Generate an AI image for each selected point if image generation is available.
11. If image generation is not configured, generate a placeholder image containing the prompt text.
12. Insert each image into the video for about 3 seconds.
13. Keep the original narration audio playing underneath the inserted image.
14. Export the final edited video.

## Suggested Tools

- Python
- `yt-dlp`
- `faster-whisper` or `whisper`
- FFmpeg
- An LLM for translation and deciding image insertion points
- An image-generation API if available

## Output

```text
output/
├── transcript.txt
├── transcript.json
├── subtitles_zh.srt
├── ai_visuals.json
├── visuals/
│   ├── visual_01.png
│   └── visual_02.png
└── final.mp4
```

Example `ai_visuals.json`:

```json
[
  {
    "start": 18.2,
    "end": 21.2,
    "reason": "abstract concept needs visual explanation",
    "prompt": "Educational diagram showing a black hole event horizon with arrows indicating light cannot escape"
  }
]
```

## Project Structure

```text
project/
├── produce.py
├── requirements.txt
├── README.md
├── core/
│   ├── download.py
│   ├── transcribe.py
│   ├── translate.py
│   ├── visuals.py
│   ├── compose.py        # 唯一的繪製程式：讀 scene.json 燒成影片
│   ├── scene.py          # 版面即資料
│   ├── caption.py        # 字幕畫成圖
│   └── utils.py
├── work/
└── output/
```

## Scope

Keep this first version as simple as possible.

Do **not** implement:

- YouTube uploading
- Shorts cropping
- TTS
- Voice cloning
- Fact checking
- Automatic source discovery

The priority is to successfully produce `output/final.mp4` end-to-end.

If necessary, simplify aggressively:

- Use placeholder translation hooks.
- Use placeholder AI image generation.
- Use simple full-frame image insertion.

## Deliverables

1. Working code
2. `requirements.txt`
3. `README.md` with setup and run instructions
4. Example command usage
5. Notes explaining where real translation and image-generation services can be plugged in later
