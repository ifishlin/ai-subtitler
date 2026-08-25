# 專案進度

更新日期：2026-08-25

## 目標

建立 Python 影片處理 pipeline：下載或讀取新聞影片、辨識語音、產生繁體中文字幕、插入 AI 規劃的資訊圖卡，最後輸出可播放的 MP4。

## 已完成

- 下載測試新聞影片（臺灣南部豪雨）。
- 使用 faster-whisper 辨識中文語音。
- 透過 SSH tunnel 使用遠端免費 Qwen 校正字幕及規劃圖卡。
- 將字幕與 1–2 張資訊圖卡燒錄進影片。
- 輸出 H.264/AAC MP4，解決原始 AV1/Opus 在部分播放器無法開啟的問題。
- 加入專有名詞人工校正 sidecar。
- 加入漏句補充 sidecar，補回 Whisper 未辨識的街訪與電話訪問。
- 加入 `--sensitive` 模式，關閉 VAD 並降低靜音判定門檻。
- Whisper 結果會在連線 Qwen 前先存檔，避免網路失敗導致辨識結果遺失。

## 目前輸出

- 正式較準確版本：`output/final.mp4`
- 敏感模式測試版：`output/final_sensitive.mp4`
- 原本只有記者旁白版本：`output/final_reporter_only.mp4`
- 字幕：`output/subtitles_zh.srt`
- 逐字稿：`output/transcript.txt`、`output/transcript.json`
- 圖卡規劃：`output/ai_visuals.json`

## 測試影片

- 原始檔：`work/source_hlTBcnX3KZE.mp4`
- YouTube ID：`hlTBcnX3KZE`
- 原片編碼：AV1 + Opus
- 輸出編碼：H.264 + AAC

## AI 服務

- SSH：`yuyu@cuba001`
- 本機 tunnel：`127.0.0.1:11435`
- 遠端 Ollama：`127.0.0.1:11434`
- 預設模型：`qwen2.5:7b`
- 其他可用模型：`qwen3:8b`、`qwen2.5:1.5b`、`qwen3:4b`

## 執行方式

一般模式：

```bash
.venv/bin/python main.py
```

敏感模式：

```bash
.venv/bin/python main.py --sensitive
```

## 敏感模式結論

- 能多抓到部分電話訪問。
- 對臺語、口音較重或多人重疊的街訪改善有限。
- 容易增加錯字、重複字幕及時間錯位。
- 例如把「積淹水」辨識成「積煙水」。
- 因此目前不作為預設模式。

## 目前限制

- Whisper 可能漏掉臺語、低信心、電話音質或重疊語音。
- 原片已有字幕時，目前以 reviewed extra-segment sidecar 補回漏句。
- `VIDEO.corrections.json` 以 segment ID 校正；不同辨識模式可能改變 ID，之後應改用時間範圍或原文配對。
- OCR 自動讀取原片字幕尚未完整實作，目前漏句內容仍需確認。

## 建議下一步

1. 將字幕校正由 segment ID 改為時間範圍配對。
2. 自動偵測長字幕空白區段。
3. 在空白區段進行第二次敏感辨識或畫面 OCR。
4. 讓 Qwen 比對 Whisper、OCR 與上下文後選出字幕。
5. 對低信心結果加上人工審核清單，不直接發布。
