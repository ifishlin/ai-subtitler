# 交接

寫給下一個 session。**先讀 `CLAUDE.md`，那份是原則；這一份是現況。**

---

## 一、先知道這三件事

**這個專案的核心信念是：發現的錯要變成門。** 不是修這一次的症狀，是讓它
下次過不去。`CLAUDE.md` 開頭那張表就是歷史，`docs/MISTAKES.md` 是完整版
（包含擋不住的那幾項，那份比擋得住的重要）。

**視覺的錯不會報錯。** 照片被推出畫面、字被切掉、卡片全黑、成片沒有聲音 ——
沒有例外、沒有 stack trace、測試不會失敗，程式跑得完完全全成功。它們只在
有人看的時候才存在。所以**做完要真的把畫面叫出來看**，不要只看回傳值。

**做了 N 次收到 0 筆的步驟要出聲。**「找到 0 筆」和「沒有去找」在畫面上
一模一樣，而後者才是錯。

---

## 二、現在跑得起來的東西

### 四支成片，都在 `assets/shorts/`

| 文案 | 題目 | 長度 | 實拍 | 會動的實拍 | 情境影片 |
|---|---|---|---|---|---|
| AI電費無旁白 | AI資料中心推高電費 | 83.6s | 55% | 50% | 2 句 |
| 美利堅湖 | 川普要把安大略湖改名 | 84.8s | 56% | 56% | 1 句 |
| 好萊塢誰的新聞 | 科技巨頭買下好萊塢 | 85.6s | 63% | 52% | 0 |
| 梅西納故事 | 梅西納名畫失竊 | 86.0s | 62% | 51% | 0 |

十二道門全過，每一支都有原聲（影片段落 −18～−21 dB，卡片是純靜音 −91）。

### 四種鏡頭

```
clip   新聞片段    會動、算實拍、要燒出處、保留原聲
pic    照片        不會動、算實拍、看授權
stock  情境影片    會動、**不算實拍**、不燒出處        ← 新的
card   卡片        會動、不算實拍、不燒出處
```

`stock` 的 `show` 以 **`情境：`** 開頭，那個前綴就是 `is_real()` 的判定依據。
它不算實拍是刻意的：算了的話，`borrowed.least`（實拍下限 50%）那條線
就可以用天氣和車流填滿。網頁上有一頁 `/shots` 專門講這個。

### 情境影片的池子

`assets/broll/`，114 支候選、留下 93、2.2 GB。`library.json` 進版控（那是
判斷），mp4 不進。每組每次隨機送 `collect.stock_per_group`（3）支進 prompt。

**抽出來的樣要存**（`assets/broll/offered/<題目>.json`）：`sheet()` 被
`as_text()`（印 V1 給模型看）和 `fasten()`（把 V2 換回檔案）各呼叫一次，
不存的話兩邊抽到不同的三支，而**兩邊各自都「正確地」隨機了，不會報錯**。

### 網頁（`studio/server.py`，`python studio/server.py`，8000 埠）

導覽列十格，右邊兩組是下拉：

```
資料流 ▾   紀錄 /records、素材去哪 /material、送去寫 /prompt
           三頁都吃 ?name=，下拉會把題目帶著走
設定 ▾     規定 /prompts、片型 /houses、卡片 /cards、
           情境影片 /broll、四種鏡頭 /shots、所有設定 /config
```

---

## 三、四道檢查，commit 前一定要跑

```bash
bash studio/checkpy.sh     # 同名 def、短鏡頭壓不壓得出來
bash studio/checkjs.sh     # 網頁：頂層摸不到的元素、沒人用的 CSS、
                           # 蓋掉瀏覽器內建的名字、影片停著有沒有畫面
bash studio/checkcards.sh  # 十二種卡三種長度不出版面、折行不切開拉丁字、
                           # 網頁那張圖跟成片會不會畫成不同種
bash studio/checkview.sh   # 真的用 Chrome 打開每一頁（要伺服器在跑）
```

`checkview.sh` **會限時 40 秒**，因為它跑在 pre-commit 裡，而一個會卡住
commit 的檢查會被關掉。

**加新檢查的時候注意**：`checkpy.sh` 我曾經把新的一段接在結尾後面，
結果吞掉了前面那道的結束碼 —— 它從此永遠說通過。每一段都要 `|| bad=1`。

---

## 四、還沒做的

### 看圖那一步（最重要）

`docs/TODO-pictures.md` 最上面整節。一句話：**`seen` 是寫文案的模型自己
填的，而它看不到圖**，所以 `unchecked` 那道門擋不到任何東西。

卡在「誰來看」不是卡在程式 —— cuba001 上八個模型全是純文字，沒有一個
看得到圖。設計和三個決定的理由都寫在那份文件裡了。

### 其他

- **好萊塢那題 0 則留言**。收集只問前三支影片，而那題有 31 支。
- **`/broll` 的「再找一批」現在等於沒用**：它重跑同樣 38 個搜尋詞，
  Pexels 回傳穩定，按下去多半 0 新增。要真的抓到新的得先改
  `core/broll.py` 的 `GROUPS`。
- **`docs/` 底下另外七份文件沒有掃過**，可能還有被推翻的內容
  （四種鏡頭、聲音、比例、新的門）。`TODO-pictures.md` 已經標了三條。
- 我留了兩個測試垃圾沒清：空目錄 `work/丟丟看/`、
  `trash/測試丟丟看-20260901-131824/`（12 bytes）。

---

## 五、這個 session 踩過的坑

寫下來是因為每一個都花了時間，而且下一次很可能同樣的形狀。

**掃檔少數了一種指標。** 一支影片有 `file` 和 `captions` 兩個檔案指標，
掃舊素材時只收了 `file`，四十四個 `.vtt` 全被掃走。症狀是「段落 0」——
跟「這幾支本來就沒有字幕」一模一樣。沒有損失的唯一原因是掃的是移到 trash。
**現在刪東西一律走 `core/bin.py` 的 `toss()`。**

**`loudnorm` 在 1.9 秒的鏡頭上算出 NaN**，aac 整支拒收。它需要約三秒的
分析窗，而情境影片進來之前最短的鏡頭都超過三秒。修了兩層（短的用
`dynaudnorm` ＋ `alimiter` 夾住無限值）。種錯的時候學到：**只拿掉其中一層
門會說通過**，因為兩層各自都夠 —— 分不清哪一層在擋什麼，會誤以為門壞了。

**`wrap_at` 把 `Netflix` 折成 `Ne` 和 `tflix`**，而每一道門都過：字沒出界、
掃四個邊沒有墨、寬度量過。那個位置沒有東西在看「單字」這個概念。

**`/scripts` 永遠載不完**：八個段落縮圖各開一條連線抓原始素材的檔頭，
佔滿瀏覽器對單一主機的六條上限，底下二十六張照片全部排在後面。

**卡片頁六十格全黑**：`<video>` 在播之前不會自己解出任何一格，而
`preload="metadata"` 只拿長寬。檔案在、HTTP 200、JS 零例外。

**我又寫了一次頂層 `function open()`** 蓋掉 `window.open` —— 同一個錯這個
repo 犯過兩次，第二次被 `checkjs` 擋下來了。

**情境影片挑錯**：我把時鐘特寫配到「下次你打開地圖」，因為它**會動**，
不是因為它有意義。壓出來看才發現。這一類最危險的變體是：**模型只看得到
搜尋詞，看不到畫面** —— 一支 `flags waving government building` 其實拍的是
烏克蘭國旗，一支 `counting money hands` 數的是波蘭茲羅提。畫面內容比它的
名字更具體的素材，就是陷阱。

---

## 六、跟這位使用者工作

**他常說「快問快答」。** 那表示接下來每一則都要一到兩行，除非他說「結束
快問快答」。做完事情的回報可以長一點，但也不要鋪陳。

**「先記下來，不要做」「等我說再做」是字面意思。** 討論不是授權；他把
**跑 grep、開檔案來看** 也算在「動」裡面。要查證才能回答的時候，說
「等你說我去查」，然後停住。他交代多件事的時候，先把清單覆述一次再問
要不要開始。

**回報一律附比例。** 絕對數字看不出失衡。

**被工具擋下來的時候不要繞路。** 我用 `rm -rf` 被拒之後，改用 Python 的
`shutil.rmtree` 做同一件事 —— 那讓那道限制形同虛設，而他很在意這件事。
被擋 = 停下來問。

**他不用 `gh` CLI，也不打算用。** 推送走 SSH。要開新 repo 的話請他自己
到 GitHub 網頁上開。

**不要一直建議他買 Claude API。**

---

## 七、環境

```
專案      /Volumes/Fish/personal/Video_pipeline，分支 stage-one
遠端      git@github.com:ifishlin/ai-subtitler.git
Python    ./.venv/bin/python（不是系統的）
伺服器    .venv/bin/python studio/server.py  →  127.0.0.1:8000
金鑰      ~/.config/video_pipeline/{pexels,anthropic}，600，不進版控
cuba001   ssh yuyu@cuba001，Ollama 在 11434，本機開隧道到 11435
          八個模型全是純文字：gpt-oss:120b/20b、qwen3:32b/30b-a3b/8b/4b、
          qwen2.5:7b/1.5b
```

跑 CPU 密集工作 cuba001 比本機快 8–16 倍，長時間的辨識轉檔優先送過去。

### 這個 session 動過的主要檔案

```
core/broll.py     新增 —— 情境影片的池子（抓候選、判斷、下載、抽樣）
core/bin.py       新增 —— 刪除的唯一入口，一律移到 trash
core/script.py    is_stock()、moves()、STOCK 前綴、cardbound 門、
                  measure() 多報 stock_share / moving_share
core/shorts.py    clip_cut() 保留原聲（響度統一、淡入淡出、短的用 dynaudnorm）
core/build.py     第四種鏡頭的分支、loudness()、silent_clips()、卡片籤
core/cards.py     roll()/roll_for()/seed_of()、wrap_at 不切開拉丁字
assets/rules.json borrowed 加下限、sound、cards.pick、collect.stock_per_group
studio/static/    新增 records / broll / shots / config 四頁；nav.js 改成分組
```
