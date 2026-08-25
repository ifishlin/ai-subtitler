# AI Video Pipeline

This local Python pipeline downloads or reads a video, transcribes speech, creates Traditional Chinese subtitles, asks a remote Qwen model to plan one or two information cards, and renders the cards and subtitles into a final MP4 while preserving the original narration.

## Requirements

- macOS with Python 3.12, FFmpeg and SSH
- SSH access to `yuyu@cuba001`
- Remote Ollama with `qwen2.5:7b`

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The pipeline opens this tunnel automatically when needed:

```bash
ssh -f -N -L 11435:127.0.0.1:11434 -o ExitOnForwardFailure=yes yuyu@cuba001
```

## Run

Use the downloaded test video:

```bash
.venv/bin/python main.py
```

The default transcription model is `medium`. Use `--whisper-model small` for a faster, less accurate draft.
Use `--sensitive` to disable VAD and lower the silence threshold for street interviews.

Or provide a URL or local file:

```bash
.venv/bin/python main.py "https://www.youtube.com/watch?v=VIDEO_ID"
.venv/bin/python main.py "/path/to/video.mp4"
```

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

Cards appear temporarily on the right side of the original picture. The original audio continues underneath. See `AI_SERVICE.md` for the remote model configuration.

## Subtitle review UI

A standalone local web page for proofreading the finished subtitles and
re-burning the video, without re-running this pipeline:

```bash
.venv/bin/python subtitle_editor/server.py
```

It reads `output/` and `work/` and writes only `output/subtitles_zh.reviewed.srt`,
`output/final_reviewed.mp4` and `editor_cache/`. See `subtitle_editor/README.md`.

For publish-quality proper nouns, an optional sidecar named `VIDEO.corrections.json` can map transcript segment IDs to reviewed text. The included test video has such a review file under `work/`.

If Whisper misses accented, Taiwanese, overlapping, or telephone speech, an optional
`VIDEO.extra_segments.json` sidecar can add reviewed `{start, end, text}` captions.
These are merged and renumbered automatically before cards and video rendering.
