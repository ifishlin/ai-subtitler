# 新聞來源

清單存在 `assets/sources/media.json`，這份文件是那份清單的「讀法」——
哪些家、抓不抓得到、抓不到的時候是哪一種擋法。程式只認 `media.json`，
這份文件不會被任何程式讀，改清單要去改那邊。

## 目前問的是哪些家

`regions` 沒填的題目，問美國 11 家 + 歐洲 8 家；通訊社兩家永遠問。
題目自己標 `regions: ["DE"]` 才會問德國那 9 家。

### 美國（US）

| 媒體 | 立場 | 種類 | 付費牆 | 備註 |
|---|---|---|---|---|
| AP News | neutral | 通訊社 | 否 | |
| Reuters | neutral | 通訊社 | 否 | **目前抓不到正文**——不是付費牆，是 DataDome 機器人牆，見下面 |
| PBS NewsHour | centre-left | 公共 | 否 | |
| NPR | centre-left | 公共 | 否 | |
| CNN | left | 電視 | 否 | |
| MS NOW | left | 電視 | 否 | 原 MSNBC |
| Fox News | right | 電視 | 否 | |
| Fox Business | right | 財經 | 否 | |
| Wall Street Journal | right-editorial | 財經 | **是** | **目前抓不到正文**——要訂閱，YouTube 影片公開 |
| Bloomberg | neutral | 財經 | **是** | **目前抓不到正文**——部分免費 |
| CNBC | neutral | 財經 | 否 | |

### 歐洲（EU）

| 媒體 | 立場 | 種類 | 付費牆 | 備註 |
|---|---|---|---|---|
| BBC News | neutral | 公共 | 否 | |
| Financial Times | neutral | 財經 | **是** | **目前抓不到正文**——要訂閱，YouTube 影片公開 |
| The Economist | market-liberal | 財經 | **是** | **目前抓不到正文** |
| The Guardian | left | 報紙 | 否 | |
| Deutsche Welle | neutral | 公共 | 否 | 德國 |
| France 24 | neutral | 公共 | 否 | |
| Euronews | neutral | 電視 | 否 | |
| Al Jazeera English | non-western | 電視 | 否 | 非西方視角，常補到英美媒體不提的角度 |

### 德國（DE，只有題目自己標 regions 才會問）

| 媒體 | 立場 | 種類 | 付費牆 | 備註 |
|---|---|---|---|---|
| tagesschau | neutral | 公共 | 否 | ARD 的新聞旗艦 |
| ZDFheute | neutral | 公共 | 否 | |
| MDR | neutral | 公共 | 否 | 薩克森、圖林根、薩克森-安哈特 |
| DER SPIEGEL | centre-left | 報紙 | **是** | |
| Süddeutsche Zeitung | centre-left | 報紙 | **是** | 頻道是 @sueddeutsche，@SZ 沒有搜尋頁 |
| FAZ | centre-right | 報紙 | **是** | |
| WELT | right | 電視 | 否 | 新聞頻道是 @WELTVideoTV |
| BILD | right | 報紙 | 否 | 小報，數字常誇大，但報的事本身通常有 |
| ntv | neutral | 電視 | 否 | |

德國那三家付費牆（SPIEGEL、Süddeutsche Zeitung、FAZ）**沒有標「目前抓不到」**——
不是抓得到，是還沒有題目用過 `regions: ["DE"]`，這三家從來沒被真的問過一次，
沒有測試結果可以寫。跟上面美國、歐洲那幾家「試過，確認抓不到」是不同的狀態，
不要混在一起看。

### 圖表用的原始數據（不算報導，另外一份清單）

Fiscal Data（美國財政部）、CBO、FRED（聯準會）、PGPF、Eurostat——新聞畫面角落
標出處的圖表，順著回去拿原圖，比搜圖精準。

## 抓報導正文，四步，只有最後一步是 LLM

```
1. hunt_reports()          查 Google News RSS，找出每家寫了哪幾篇   純程式
2. article.real_url()      Google News 的轉址 → 出版社真正的網址   純程式
3. article.browser_fetch() 用 curl 把整頁網頁抓下來                純程式
4. trafilatura.extract()   從整頁挑出正文，去掉選單、廣告、留言板  純程式
─────────────────────────────────────────────────────────────
5. facts.ask_report()      把正文丟給模型，整理成幾條事實、翻成中文   LLM
```

抓不抓得到，全部發生在 1–4，跟 LLM 無關。LLM 只負責「讀懂+摘要+翻譯」，
不負責「把網頁變成文字」。

## 抓不到，分兩種——付費牆 vs 機器人牆

`media.json` 的 `paywall` 欄只回答第一種。第二種它答不出來，因為同一家
媒體可能兩種都沒有、也可能兩種都有，欄位不夠表達。

**付費牆**：`paywall: true` 的幾家（WSJ、Bloomberg、FT、Economist、
SPIEGEL、Süddeutsche Zeitung、FAZ）——正文要登入訂閱帳號才看得到，
現在的抓法完全沒有登入這回事，所以抓不到是預期中的事，不是 bug。
要抓到就得真的訂閱、把登入後的 session cookie 餵給抓取程式，是另一件事，
沒做。

**機器人牆**：`paywall: false`，理論上任何人都能看，但擋自動化工具。
這一種底下還分兩種，難度差很多：

| | 擋法 | 能不能繞過 | 現況 |
|---|---|---|---|
| Cloudflare（例：AP News） | 看連線工具的 TLS 握手像不像真瀏覽器，跟表頭無關 | 換一個真的用 libcurl 連線的工具就過 | **2026-09-05 已修**，見下面 |
| DataDome（例：Reuters） | 要求瀏覽器真的執行一段 JavaScript 算出答案 | 需要真的會跑 JS 的瀏覽器（Playwright 之類），不是換個連線工具能解的 | **還沒解決**，見下面 |

### Cloudflare 這一種：AP News，已經修好

`core/article.py` 原本用 `trafilatura.fetch_url()` 抓正文，AP News 一律
拿不到（回傳 `None`）——連幫它把 User-Agent 換成瀏覽器字串都沒用，因為
擋的不是表頭，是 `trafilatura` 底層 `urllib3` 的 TLS 指紋長得不像瀏覽器。

同一支網址換用 `article.browser_fetch()`（真的呼叫系統的 `curl`）就直接
拿得到整頁。已經改進 `fetch()`，兩篇 AP 的文章都驗證抓得到：

- 《Trump signs order renaming Lake Ontario as 'Lake America'...》→ 4207 字
- 《Lake Ontario now called Lake America on Google Maps...》→ 1998 字

這是共用函式，不是只修這一篇——以後任何題目、來源是 AP News，會自動套用
同一個抓法，除非 AP 以後把防護等級調高（例如換成跟 Reuters 一樣的
JavaScript 驗證），那時候現在這個解法會失效，要重新處理。

### DataDome 這一種：Reuters，還沒解決

兩篇都試過 `browser_fetch()`，拿回來的整頁只有 771 字，是驗證頁本身
（`Please enable JS and disable any ad blocker`），不是文章：

- 《Google Maps now show 'Lake America' in US, not 'Lake Ontario'》
- 《Trump renames Lake Ontario as 'Lake America' amid...》

試過另一條路——查 Wayback Machine 有沒有存檔，其中一篇真的有，抓下來一看，
**存檔裡存的也是同一張驗證頁**，代表連 Internet Archive 的爬蟲那次也被擋了。

要解只能上真的會執行 JavaScript 的瀏覽器（例如 Playwright 操控 Chromium），
這台環境目前沒裝（`playwright`、`selenium` 都沒有），而且裝了也不保證
過關——DataDome 專門會抓「這是不是一個被自動化操控的瀏覽器」。是不是要裝
這一整套工具，等下次真的需要再決定，不要因為抓不到兩篇就先裝。

## 下次遇到抓不到，先這樣判斷

1. 先看 `media.json` 那家的 `paywall` 是不是 `true`——是的話不用查，
   本來就抓不到，正常。
2. `paywall: false` 但還是抓不到：用 `article.browser_fetch(url)` 手動
   抓一次，看回來的整頁長什麼樣：
   - 幾百字、內容像「請開啟 JavaScript」「disable ad blocker」→ 機器人牆，
     再看是不是已經含 `dd={'rt':...}` 這種字串（DataDome）——是的話跟
     Reuters 同一種，目前解不了。
   - 完全空白、或整頁都是正常文章但太短 → 可能是別的原因（斷線、正文
     太短被 `LEAST_CHARS` 擋），不是機器人牆。
3. **只有第 1 種（Cloudflare 式）值得花時間修**——現在 `browser_fetch()`
   本來就是為了那一種寫的。第 2 種（DataDome 式）是「需要真瀏覽器」，
   不是「換個寫法」能解的問題，先回報清楚是哪一種，不要在裡面硬試表頭
   組合。
