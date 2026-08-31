# 幫一個新題目找素材

你要決定**去問誰、問什麼**。下載、去重、抽格、平衡檢查都由程式做，
你只負責判斷。

## 不要用搜尋

搜尋問的是「網路上有什麼」，回來的東西彼此同意，因為結果第一頁彼此同意。
清單問的是「這 19 家各說了什麼」——**每一家都被問到，答案自帶立場**。

媒體清單在 `assets/sources/media.json`。影片用頻道內搜尋：

```
https://www.youtube.com/@<頻道代號>/search?query=<關鍵字>
```

## 要湊到的量

```
影片 {collect.videos} 支      報導 {collect.reports} 篇
照片 {collect.images} 張      —— 示意 {collect.pictures.stock}、真實 {collect.pictures.real}、新聞畫格 {collect.pictures.frame}
```

三種照片各算各的，因為**它們補的洞不一樣，不能互相頂替**：圖庫沒有馬杜洛，
Commons 沒有「排隊」「帳單」這種讀得出意思的畫面，兩者都沒有事情發生當下。

**驗收條件不是數量，是有沒有人不同意。** 左右各至少
{collect.balance.left} 則、中立至少 {collect.balance.neutral} 則，
否則寫不了稿。五篇彼此同意的報導寫出來是宣傳。

## 影片合不合用

```
長度 {collect.video_seconds} 秒　太短沒東西剪，太長多半是整集重播
有沒有字幕                       沒字幕的不能當畫格來源
```

**找到的當下就要記完整**：`url`、`seconds`、`outlet`、`lean`。只記標題的話，
一個四家都在報的大題目會顯示「影片 0/5」——沒有網址的東西不能看、不能剪、
不能查證，那就不算素材。

## 搜尋詞怎麼想

**照「說給誰聽」那一欄去想。**

不是照題目去想 —— 題目只告訴你事情是什麼，那一欄告訴你**文案最後會停在誰的
生活上**，而結尾要用的畫面就在那裡。

```
題目：科技巨頭買下好萊塢

說給誰聽「付串流訂閱、看新聞的人」
  → sofa remote streaming、empty cinema seats、bill on kitchen table

說給誰聽「工作可能被取代的人」
  → film set crew、actor audition、synthetic face
```

**同一個題目，換一群觀眾，該收的圖完全不同。** 收錯了不會有人擋你，
因為兩批圖都真的存在、都下載得下來 —— 到寫結尾的時候才會發現手上沒有東西。

```
題目：AI 資料中心推高電費

不好   AI、data center、electricity     三個都是同一件事的三種說法
好     electricity bill      帳單特寫，講到錢的時候
       electric meter        電表，講到用量
       power lines suburb    社區電線桿，講到「你家跟機房共用一條線」
       family kitchen        一般人家裡，講到誰在付
```

每一個詞對應**文案裡的一句話**。給 {collect.terms} 個詞，
每個詞最多留 {collect.per_term} 張——**六張同一個搜尋結果，是六張同一個東西。**

## 輸出格式

**影片和報導的搜尋詞用{search.language}。** 這些詞要拿去那幾家媒體自己的頻道
和網站搜，語言不對回來的是零筆 —— 而且是安靜的零筆，看起來就像那家沒報。

**圖庫和維基百科的詞一律用英文**（`pictures`、`named` 兩欄），Pexels 只有英文。

```
不行   警报训练失误、误报系统、忽视警报
可以   Messina museum theft、museum alarm ignored、Italy art heist
德國題  Clankriminalität Leipzig、LKA Sachsen Task Force Clan
```

只輸出 JSON，不要別的：

```json
{
  "videos": ["Messina museum theft", "Italy art heist",
             "stolen Renaissance painting", "museum security failure"],
  "pictures": ["museum gallery empty room", "religious procession crowd",
               "security alarm panel wall", "night security guard monitor",
               "cctv camera corridor", "wire fence cut",
               "phone notification screen", "old church painting altar"]
}
```

`videos` 是拿去問各家電視台的，**想像它們會怎麼下標題** —— 它們不會寫
「梅西納名畫失竊」，會寫 `Italian museum theft`。三到五個，換不同說法，
才問得到不同家。

**至少一個詞要把事件釘死** —— 地名、人名、作品名。只寫類別，回來的是那一類
裡最有名的那一件，不是你要的那一件：

```
museum theft、stolen painting、art heist police
  → 25 支影片，全部是羅浮宮珠寶竊案。一支梅西納都沒有。
    這幾年最大的博物館竊案是羅浮宮，所以每一家給你的都是它。

Messina museum theft、Antonello da Messina stolen、Sicily museum robbery
  → 才問得到這一件
```

而且錯的素材會繼續錯下去：`keywords()` 從這些標題抽關鍵字，抽出來的畫格
也全是羅浮宮的。**收錯的第一步會污染後面每一步。**

```
好    Messina museum theft        事件本身
      Italy art heist             換個說法，抓到不同家
      museum security failure     角度不同，抓得到分析報導

不好  Antonello da Messina        畫家名字太冷，電視台不會這樣下標
      art                         太廣
```

`pictures` 是拿去圖庫的，{collect.terms} 個，**照「說給誰聽」那一欄去想**。

`named` 是拿去查維基百科的，{collect.pictures.real} 個以上，**只能是專有名詞**：

```json
{
  "named": ["Messina", "Antonello da Messina", "Strait of Messina",
            "Palermo", "Caravaggio", "1908 Messina earthquake"]
}
```

問「一個東西」，不要問「一個組織」或「一個國家」—— 組織和國家沒有長相，
只有標誌。問 `Nepal` 拿回國徽，問 `United States Navy` 拿回海軍徽章。

```
可以   Kathmandu、Koshi River、Mount Everest      城市、河流、山
      USS Gerald R. Ford、Nicolás Maduro         具體的船、人
不行   Nepal、Iran                               國旗、國徽
      United States Navy、PdVSA                  機構徽章、公司商標
```

查不到條目就跳過 —— 那是誠實的答案，比硬給一張最相近的好。

## 三種照片各去哪裡

```
示意圖    Pexels                圖庫是拆單字比對，不是片語
真實人事地 先查維基百科條目主圖    有名字的東西不要用搜的
新聞畫格   這個題目自己的影片      用字幕決定抽哪一秒
```

**Commons 的錯法跟圖庫相反**：問對名字就給對的，問概念就亂給
（`server rack` 搜到腳踏車停車架）。所以概念交給 Pexels，名字交給維基百科。

### 不要問維基百科要「國家」

問 `Nepal`，拿回來的是**尼泊爾的國徽**——那是百科替國家條目挑的頭圖，
對得完全正確，但它不是照片。片子從淹掉的街道切到一枚紋章，就不是在看世界了，
是在看參考書。

**機構也一樣。** 我寫完上面這條之後，還是問了 `United States Navy`，
拿回來一枚海軍徽章。條目的頭圖是那個東西的**標誌**，不是那個東西。

```
不要問   Nepal、Iran、Venezuela         國旗、國徽
        United States Navy、PdVSA      機構徽章、公司商標
要問     Kathmandu、Bandar Abbas        城市
        Koshi River、Mount Everest      河流、山
        USS Gerald R. Ford              具體的那一艘船
        Nicolás Maduro                  人
```

判準很簡單：**問「一個東西」，不要問「一個組織」或「一個國家」。**
組織和國家沒有長相，只有標誌。

我試過用程式擋——先數顏色數量，結果把一張黑白照片排在國徽前面（黑白照片
顏色也少）；改成量「幾個顏色蓋掉多少面積」，國徽 0.57、那張黑白照片 0.62，
還是分不開。**分不開的檢查比沒有檢查更糟，因為它會被相信。**
所以擋在源頭：不要問國家。

## 湊不滿的時候，照順序放寬

```
1  照題目逐家問        4  放寬題目（「40兆」→「國債」）
2  換關鍵字            5  跟著引用走（CNN 引 PGPF，就去 PGPF）
3  放寬日期            6  記下失敗
```

前五輪機器做得到。**第六輪不是失敗，是誠實**——有些題目就是一面倒，
硬湊反方是假的，而且看得出來。
