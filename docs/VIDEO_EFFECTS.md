# 影片效果：ffmpeg 能做的調整

備忘，2026-08-27。討論過但**暫時不做**，等版面和字幕穩定後再說。

全部都是 ffmpeg 濾鏡，接在 `src/compose.py` 的 filter graph 裡。因為壓片本來就在
重新編碼，加這些**幾乎不增加時間**。

## 1. 基本調光 —— 最實用，建議第一個做

一個 `eq` 濾鏡包含全部：

```
eq=brightness=0.06:contrast=1.12:saturation=1.15:gamma=1.0
```

| 參數 | 範圍 | 說明 |
|---|---|---|
| `brightness` | −1.0 ~ 1.0 | **加法**，不是倍率。0 是原樣 |
| `contrast` | −1000 ~ 1000（實用 0.5~2） | 1.0 是原樣 |
| `saturation` | 0 ~ 3 | 0 是黑白，1.0 是原樣 |
| `gamma` | 0.1 ~ 10 | 1.0 是原樣。調中間調不動黑白點 |

在 scene.json 裡的位置，掛在 video 元素上：

```json
{ "id": "video", "type": "video", "box": [48, 48, 1200, 696],
  "adjust": { "brightness": 0.06, "contrast": 1.12, "saturation": 1.15 } }
```

**網頁預覽**用 CSS `filter`，可以即時拉桿：

```css
filter: brightness(1.06) contrast(1.12) saturate(1.15);
```

注意 **CSS 的 brightness 是乘法，ffmpeg 的是加法**，要換算：
CSS 的 `brightness(1 + b)` ≈ ffmpeg 的 `brightness=b`（b 小時接近，大時會偏）。
contrast 和 saturation 兩邊定義相同，可以直接對應。

## 2. 色調

| 濾鏡 | 用途 |
|---|---|
| `colortemperature=temperature=5500` | 色溫，偏暖／偏冷 |
| `colorbalance=rs=.1:gm=-.05:bh=.08` | 分別調高光／中間調／陰影的 RGB |
| `hue=h=10:s=1.1` | 色相旋轉 |

## 3. LUT —— 一鍵套風格

`.cube` 檔是顏色對照表：記錄「原本這個顏色換成哪個顏色」，3D 格點（常見
33×33×33）加內插。所謂「電影感」「日系」「復古膠片」都是這個。

```
lut3d=file=looks/teal_orange.cube
```

和 `eq` 的差別：`eq` 是全域算式，LUT 可以只動特定顏色（只讓暗部偏青、只讓
膚色變暖）。

**預覽的問題**：CSS 沒有查表換色這種濾鏡，瀏覽器要即時預覽只能寫 WebGL
shader —— 影片得換成 canvas，播放控制和上面疊的字幕圖層次都要重做，幾百行
圖形程式，不值得。

**便宜的替代**：按一下，後端用 ffmpeg 把目前這一格套上 LUT 輸出成 PNG
（約 0.3 秒），網頁顯示。要比較就一次產生幾張並排。而且這是**精確**的，不是
近似 —— 就是 ffmpeg 自己算出來的。

## 4. 跟版面有關的

- **模糊放大墊底**：把影片放大、模糊，當背景取代現在的淡藍色。Shorts / Reels
  最常見的做法。
  ```
  [0:v]scale=1920:-1,boxblur=40:2,crop=1920:1080[bgv]
  ```
- **暗角** `vignette=PI/5`
- **圓角／陰影**：影片先 `scale`，再用一張圓角遮罩 `alphamerge`，或直接在
  `overlay` 前墊一張帶陰影的 PNG
- **淡入淡出** `fade=t=in:st=0:d=0.5`
- **緩慢推近（Ken Burns）** `zoompan`

## 5. 銳利化

```
unsharp=5:5:0.8:5:5:0.0
```

YouTube 壓縮後會糊，先銳利一點有幫助。放在 filter graph 的**最後**。

## 建議的順序

1. `eq` 調光 —— 即時預覽、可預測、投報率最高
2. 模糊墊底 —— 版面立刻不一樣
3. LUT —— 當「一鍵套風格」，配靜態預覽圖
4. 其他看需要
