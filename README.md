# AI Video Pipeline

Turns a foreign-language video into a Chinese-subtitled explainer. It is built
in two stages, and the split matters more than any single feature.

## 兩個階段

**第一階段：自動產生影片。目標是不要人為介入。**

給一個網址，回來就是一支可以直接發布的影片。中間一個按鍵都不用按。

`produce.py` 的八個步驟就是這個階段的骨幹。新的能力接在這根骨幹上，不另起爐灶：

```
[1/8] 準備影片        yt-dlp 下載或直接讀檔，抽出音軌
[2/8] 辨識            faster-whisper 聽一次，再只重聽安靜的段落補句
[3/8] 連 LLM          決定這一次由誰回答（qwen / claude / none / replay / ask）
[4/8] 校正            修辨識的錯字和專有名詞，然後翻成繁體中文
[5/8] 檢查與自動修補   對音軌稽核字幕，機械式問題自己修；修完更差就整批退回
[6/8] LLM 審查成品     讀最後要出去的那一份，不是草稿
[7/8] 規劃圖卡        決定哪裡放卡、卡上寫什麼
[8/8] 燒錄            字幕和圖卡畫成畫面，輸出 final.mp4
```

**做不到全自動的地方就是這個階段的缺口**，而不是「留給人去處理」——每一次需要有人插手，
都應該當成第一階段的待辦。

### 第 7 步是現在最大的缺口

規劃圖卡目前只被允許整理逐字稿裡講過的事——`core/visuals.py` 的提示詞明文寫著
「不得補充、推測或捏造」，而且從第一個 commit 起就是這樣。所以它做得出
「他說了什麼」的卡片，做不出「他說的這個東西是什麼」的卡片。

最初的規劃書（`docs/codex_video_pipeline_mvp.md`）要的是後者：

```
→ use AI to identify 1–2 places where an explanatory image would help
→ generate an AI image for each selected point
```

要補上這個，第 7 步得長出三件現在沒有的能力：**判斷觀眾在哪裡會卡住**、
**去外面查**、**把查到的壓縮成一張看得懂的卡**——而且每一張都要說得出資料從哪來，
不然新聞影片捏造事實比沒有卡片更糟。當初那句禁令正是為了擋這個風險。

已經有的積木：`core/stock.py` 會查並下載影片素材（Pexels / Pixabay，功能完整但
還沒接進 pipeline）、`core/llm.py` 是問模型的管道、`tools/make_card.py` 加七個模板
負責把答案畫成卡。缺的是中間那顆決定「該解釋什麼、答案是什麼」的腦袋。

**第二階段：編輯器，用來修改第一階段的成品。**

`studio/` 是一個本機網頁。它不重跑 pipeline，只修改已經產出的東西：改錯字、
調字幕時間、排版面、放圖卡和素材影片、剪掉不要的片段、把幾支影片接起來，然後重新燒錄。

**如果第一階段的成品能直接交付，第二階段就不需要存在。** 它是安全網，不是目標。
現階段完全不用改很難，所以編輯器有用；但每一次你打開它，都是第一階段的一張待辦單。

```
一個網址  ──[第一階段 produce.py]──▶  可以直接發布的影片
                                          │
                                          └──[第二階段 AI-Desk]──▶  修過的成品
                                              （第一階段不夠好時才打開）
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

## Run（第一階段）

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

## Output

## 目錄結構

Four roles, and nothing sits in two of them:

```text
produce.py            程式碼　pipeline 的進入點
core/                         pipeline 的實作
studio/                       編輯器：伺服器和兩張網頁
tools/                        零星的小工具

work/                 輸入　　拿進來的原片，和人工寫的 sidecar

assets/               素材　　跨專案共用，可以拖到任何一支影片上
├── images/                   圖片和畫好的資訊卡
├── cutouts/                  去背版（從 images/ 產的）
├── cards/                    資訊卡的 HTML 原稿和模板
└── clips/                    素材影片

projects/             輸出　　一次 run 一個資料夾
└── 名字/
    ├── run.json              ← 這是專案的記號：說明它是哪支影片
    ├── transcript*.json      辨識和校正的文字
    ├── subtitles_*.srt       字幕
    ├── scene.json            版面
    ├── visuals/              這一支影片自己的圖卡
    └── final.mp4             成品

editor_cache/         衍生　　proxy、波形、縮圖、字幕圖、抽出的音軌
trash/                        刪掉但還沒真的丟的東西
docs/                 文件
```

The rule is that anything under `editor_cache/` can be deleted at any time and
will be made again on demand, and nothing else can. That is the whole reason
for the split: when the disk fills up you should not have to work out which
264 megabytes are safe to lose.

Every run gets its own directory under `projects/`. What makes one a project
is the `run.json` inside it, which says which video the run is about -- not
what the directory is called, so a project can be named after its subject.

```text
projects/RFK訪談/
├── run.json            ← which video this run is about
├── transcript.txt
├── transcript.json
├── subtitles_zh.srt
├── ai_visuals.json
├── visuals/
│   ├── visual_01.png
│   └── visual_02.png
└── final.mp4
```

Cards appear temporarily on the right side of the original picture. The original audio continues underneath. See `docs/AI_SERVICE.md` for the remote model configuration.

## 第二階段：AI-Desk

A local web page that edits what the first stage produced, without re-running
any of it -- subtitles, layout, cards, footage, cuts, and joining videos
together. Open it when the automatic result is not good enough; every reason
you had to open it is a gap in the first stage:

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
