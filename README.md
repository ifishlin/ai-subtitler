# AI Video Pipeline

Turns a foreign-language video into a Chinese-subtitled explainer. It is built
in two stages, and the split matters more than any single feature.

## Two stages

**Stage one: make the video automatically. The goal is that nobody has to
touch it.**

Give it a URL and get back a video that can be published as it is. Not one
button pressed in between.

`produce.py`'s eight steps are the backbone of this stage. New capability
attaches to that backbone rather than starting somewhere else:

```
[1/8] Prepare      yt-dlp downloads it, or read a local file; extract the audio
[2/8] Transcribe   faster-whisper listens once, then revisits only the silences
[3/8] Connect      decide who answers this run (qwen / claude / none / replay / ask)
[4/8] Correct      mend misheard words and proper nouns, then translate to Chinese
[5/8] Audit & mend audit the captions against the audio; mechanical faults are
                   repaired, and the whole repair is rolled back if it scores worse
[6/8] Review       the model reads what is actually going out, not the draft
[7/8] Plan cards   decide where a card goes and what it says
[8/8] Render       draw captions and cards into the picture; write final.mp4
```

**Anything that cannot be done automatically is a gap in this stage**, not
something "left for a person to handle".

### Step 7 is the largest gap

Planning cards is only allowed to arrange what the transcript already says --
`core/visuals.py` states it outright, "no additions, no inference, no
invention", and has since the first commit. So it can make a card saying what
someone said, but not one saying what the thing they said *is*.

The original brief (`docs/codex_video_pipeline_mvp.md`) asked for the second:

```
→ use AI to identify 1–2 places where an explanatory image would help
→ generate an AI image for each selected point
```

Closing that means step 7 growing three abilities it does not have: **judging
where a viewer gets stuck**, **looking it up**, and **compressing the answer
into a card** -- one that can say where its facts came from, because a news
video that invents them is worse than one with no cards at all. That
prohibition exists to hold exactly this risk shut.

What is already built: `core/stock.py` searches and downloads stock footage
(Pexels and Pixabay, complete but not yet wired into the pipeline),
`core/llm.py` is the channel to a model, and `tools/make_card.py` with seven
templates draws an answer into a card. What is missing is the judgement in the
middle -- what needs explaining, and what the explanation is.

**Stage two: an editor, for mending what stage one produced.**

`studio/` is a local web page. It does not re-run the pipeline; it edits what
came out of it -- fixing wording, retiming captions, arranging the frame,
placing cards and footage, cutting passages out, joining videos together, and
burning the result again.

**If stage one's output can be delivered as it stands, stage two does not need
to exist.** It is a safety net, not the goal. Today a video rarely needs no
changes at all, so the editor earns its place.

```
a URL  ──[stage one: produce.py]──▶  a publishable video
                                          │
                                          └──[stage two: AI-Desk]──▶  a mended video
                                              (opened when stage one fell short)
```

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

## Run (stage one)

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
measurably worse — see `docs/PROGRESS.md`.

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

## Layout

Four roles, and nothing sits in two of them:

```text
produce.py            code      the pipeline's entry point
core/                           the pipeline itself
studio/                         the editor: a server and two web pages
tools/                          odd small scripts

work/                 input     footage brought in, and hand-written sidecars

assets/               material  shared across projects, placeable on any video
├── images/                     pictures and finished information cards
├── cutouts/                    cut-out versions, made from images/
├── cards/                      the HTML each card was drawn from, and templates
└── clips/                      stock footage

projects/             output    one directory per run
└── name/
    ├── run.json                ← the marker: which video this run is about
    ├── transcript*.json        what was heard, and what it was corrected to
    ├── subtitles_*.srt         captions
    ├── scene.json              the layout
    ├── visuals/                this video's own cards
    └── final.mp4               the result

editor_cache/         derived   proxies, waveforms, filmstrips, caption
                                pictures, extracted audio
trash/                          removed but not yet thrown away
docs/                 documents
```

The rule is that anything under `editor_cache/` can be deleted at any time and
will be made again on demand, and nothing else can. That is the whole reason
for the split: when the disk fills up you should not have to work out which
hundred megabytes are safe to lose.

What makes a directory under `projects/` a project is the `run.json` inside
it, which says which video the run is about -- not what the directory is
called. So a project can be named after its subject, and renaming or moving
one does not hide it.

Cards appear temporarily on the right side of the original picture. The original audio continues underneath. See `docs/AI_SERVICE.md` for the remote model configuration.

## Stage two: AI-Desk

A local web page that edits what the first stage produced, without re-running
any of it -- subtitles, layout, cards, footage, cuts, and joining videos
together. Open it when the automatic result is not good enough:

```bash
.venv/bin/python studio/server.py
```

With no argument it opens the project worked on most recently;
`--project NAME` opens a particular one. It reads `projects/` and `work/`, and
writes inside the project directory and `editor_cache/`. See `studio/README.md`.

For publish-quality proper nouns, an optional sidecar named `VIDEO.corrections.json` can map transcript segment IDs to reviewed text. The included test video has such a review file under `work/`.

If Whisper misses accented, Taiwanese, overlapping, or telephone speech, an optional
`VIDEO.extra_segments.json` sidecar can add `{start, end, text}` captions. The sidecar
shipped for the test video was machine-transcribed from extracted audio windows and is
not line-by-line human-verified; the subtitle review UI flags those lines as such.
These are merged and renumbered automatically before cards and video rendering.
