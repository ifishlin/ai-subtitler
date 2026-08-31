# 短影音視覺

每句台詞一個鏡頭。文案與結構遵守 `script.md`。

優先順序：
1. 畫面符合台詞
2. 外部素材只能用 caption / transcript 明確支持的內容
3. 事情正在發生→影片
4. 要看清楚東西→靜圖
5. 判斷、比較、邏輯、結論→卡片
6. 避免視覺重複並滿足格式規格

## 三種鏡頭
- `clip`：現場、群眾、機具、人物正在說話或行動
- `pic`：人物、地點、文件、圖表、物件
- `card`：判斷、比較、提問、邏輯、摘要、結論

## 外部素材
外部素材 = `pic` + `clip`

- 建議：約 {borrowed.aim:pct}
- 硬上限：{borrowed.most:pct}
- 影片至少佔外部素材時間 {borrowed.clip_least:pct}
- 每三分之一影片至少一次外部素材
- {length.limit_seconds} 秒影片通常用 2–3 段 clip，每段約 4–6 秒
- 推論能用卡片清楚表達時，不要硬塞外部素材

## clip
```json
{"show":"影片：這段在演什麼","clip":"C3","seen":true}
```
- 只寫 `Cxx`
- 不寫檔名、start、end
- 起訖由程式填
- 剪出片段一律靜音
- caption / transcript 不足以確認內容時不要使用

## pic
```json
{"show":"圖片實際呈現的內容","pic":"P13","seen":true}
```
- 只寫 `Pxx`
- `show` 只能描述 caption 明確支持的內容
- 不猜資料照、新聞畫格或未提供資訊

## seen
所有 `pic` / `clip` 都必須 `"seen":true`。
意思是：提供的 caption / transcript 足以確認素材可配這一句；不足就換素材或改用卡片。

## card
卡片必須提供可直接 render 的 `card` 結構。

可用 `kind`：
- `word`：title
- `number`：title/value/under/colour/ghost
- `ring`：title/value/under
- `swap`：title/was/now
- `bars`：title/rows
- `split`：title/branches[2]
- `chain`：title/points/under
- `queue`：title/count/under
- `stack`：title/items
- `clock`：title/value/part/under
- `outro`：points/title/under/tone

選擇：
- 核心判斷→word
- 重要數字→number
- 關鍵詞→ring
- 前後改變→swap
- 數量比較→bars
- 兩條邏輯→split
- 因果/步驟→chain
- 排隊/等待→queue
- 多項清單→stack
- 時間/年數→clock

不要用只有問號、沒有資訊的卡片。

## 顏色
`colour` 與 `bars.rows` 顏色只能留空或使用 `#RRGGBB`。不得用 `red`、`warn`、`ok` 等名稱。

## tone
- `cool`：鋪陳
- `light`：翻轉
- `warm`：落地與結論

tone 跟著論證，不隨機選。

## 重複限制
- 同一 `card.kind` 最多連續 {cards.same_kind_run} 張
- 同一 `tone` 最多連續 {cards.same_tone_run} 張
- 不重複用不同卡片講同一事實
- 不為湊比例使用不相關素材
- 純推論句不要硬配外部照片

## 外部畫面文字
若 caption / transcript 明確指出畫面已有文字或數字，避免相鄰卡片出現看似衝突的資訊。
若素材資料未提供畫面文字，不要自行猜測。

## 結尾頁
最後一行必須是 `card.kind:"outro"`：

```json
{
  "say":"整件事是這樣的。",
  "role":"合",
  "show":"自製：結尾頁",
  "seconds":{ending.seconds},
  "card":{
    "kind":"outro",
    "tone":"cool",
    "points":["摘要一","摘要二","摘要三"],
    "title":"一句觀眾可以帶走的話",
    "under":"題目一句話"
  }
}
```

- `points`：{ending.points} 條，摘要支撐結論的核心內容
- `title`：觀眾能記住、轉述的一句話
- `under`：題目
- 不輸出 logo、訂閱提示或頻道標記

## 自檢
- 每句選對 `clip` / `pic` / `card`
- 外部素材有 caption / transcript 支持
- 所有 `pic` / `clip` 都有 `seen:true`
- 素材只用 `Pxx` / `Cxx`
- card 欄位合法
- colour 合法
- 外部素材與 clip 比例符合限制
- kind / tone 沒超過連續限制
- 最後一行是 `outro`
- `show` 沒描述素材無法確認的內容
