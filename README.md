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
.venv/bin/python produce.py
```

The default transcription model is `medium`. Use `--whisper-model small` for a faster, less accurate draft.

`--fill-gaps` transcribes normally, then revisits only the stretches with no
caption at all in sensitive mode. Sensitive decoding recovers Taiwanese, telephone
and overlapping speech, but applied to a whole video it also invents words, so
confining it to the silences keeps the narration accurate. Recovered lines must
pass three filters — Whisper's own silence probability, a confidence floor, and a
similarity check against nearby captions that rejects text merely repeating
narration from elsewhere. Every accepted and rejected line is written to
`gap_report.json` with its reason.

Proper nouns from `VIDEO.corrections.json` are passed to Whisper as hotwords in
this mode, which fixes them wherever they occur; the id-keyed sidecars are skipped
because filling gaps shifts every segment number after the first insertion.

`--sensitive` still applies sensitive decoding to the whole video, which is
measurably worse — see `PROGRESS.md`.

Qwen proofreads by returning a list of fragments to replace, not corrected
lines. Every edit must name text the caption already contains, must not merely
reorder it, and must sound like what it replaces — 攤方 and 坍方 are both
"tan fang", while 傳出淹水災情 and 無人路透 share no syllable. Asked for
rewritten text instead, the model moved one caption's words onto another,
swapped a correct road name for a different one, and pasted the video's
description into an interviewee's mouth; as substitutions, none of those can be
expressed. `--no-correct` skips the pass entirely.

`--output DIR` writes the results elsewhere, so a new run can be compared against
an existing one instead of overwriting it.

Or provide a URL or local file:

```bash
.venv/bin/python produce.py "https://www.youtube.com/watch?v=VIDEO_ID"
.venv/bin/python produce.py "/path/to/video.mp4"
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
.venv/bin/python studio/server.py
```

It reads `output/` and `work/` and writes only `output/subtitles_zh.reviewed.srt`,
`output/final_reviewed.mp4` and `editor_cache/`. See `studio/README.md`.

For publish-quality proper nouns, an optional sidecar named `VIDEO.corrections.json` can map transcript segment IDs to reviewed text. The included test video has such a review file under `work/`.

If Whisper misses accented, Taiwanese, overlapping, or telephone speech, an optional
`VIDEO.extra_segments.json` sidecar can add `{start, end, text}` captions. The sidecar
shipped for the test video was machine-transcribed from extracted audio windows and is
not line-by-line human-verified; the subtitle review UI flags those lines as such.
These are merged and renumbered automatically before cards and video rendering.
