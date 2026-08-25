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
- 新增字幕校對網頁 `subtitle_editor/`，對成品做校對與重新燒錄，不改動 pipeline。

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
- `VIDEO.corrections.json` 以 segment ID 校正，這個機制已經實際出錯（見下節）。
- OCR 自動讀取原片字幕尚未完整實作，目前漏句內容仍需確認。

## 已確認缺陷：corrections sidecar 錯位

用時間範圍重新比對 `transcript_raw.json` 與 `corrections.json` 後確認，
28 筆人工校正中有 **15 筆落在錯誤的段落**，另有 **1 筆被靜默丟棄**：

- `corrections.json` 的鍵值最大為 54，但套用時只有 53 段，
  所以 `"54": "陳奐宇、許政俊 臺南高雄報導"` 從未生效，
  成品仍是 Whisper 原本聽到的「公視新聞 臺南高雄報導」。
  `main.py` 用 `corrections.get(item["id"], item["text"])` 取值，找不到就沿用原文，不會報錯。
- 19–23 與 27–30 的偏移是人工重新分行造成的，內容連貫，屬正常編輯。
- **43–51 是真的壞了。** 校正落在前一段，導致同一句話在畫面上出現兩次，
  一次正確一次錯誤：

  ```
  130.12  水深及膝，仁武八德路二段     ← 校正後的文字
  132.20  水深集積 人五八的路二段      ← 未被校正的 Whisper 原文
  ```

  對 127.4–135.0 秒重新辨識可獨立確認實際語音為
  「大雨造成多區積淹水災情／阿蓮港後里有民眾拍下住家附近路段／水深及膝，仁武八德路二段一帶積淹水」，
  即成品字幕在此處確實錯位。

這個缺陷存在於 `output/final.mp4`（119–135 秒）。
用 `subtitle_editor/` 校對後重新燒錄即可修正 —— 網頁上直接編輯字幕，
不再經過 ID 對應，這類錯位無法再發生。

## 建議下一步

1. ~~將字幕校正由 segment ID 改為時間範圍配對~~ —— 校對網頁直接編輯字幕，不再需要 ID 對應。
2. ~~自動偵測長字幕空白區段~~ —— 網頁時間軸已標示。
3. 空白區的第二次敏感辨識已可在網頁上逐段執行；**畫面 OCR 尚未實作**。
4. 讓 Qwen 比對 Whisper、OCR 與上下文後選出字幕。
5. 低信心人工審核清單：網頁目前以「Qwen 改動幅度」代替信心值，
   因為 `transcribe.py` 沒有保留 `avg_logprob`。若要真正的信心值，需在 pipeline 存下該欄位。
6. 用校對網頁修掉上一節的 43–51 錯位，重新燒錄一版正式檔。
