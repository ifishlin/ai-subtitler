/* 兩頁共用的東西。
 *
 * 素材那一頁和文案那一頁本來是同一個 86 KB 的檔案。拆開之後這些函式兩邊都
 * 要用 —— 各留一份就是這個專案已經修過四次的那個錯（兩份會分岔的邏輯）。
 *
 * 只放「兩邊都真的在用」的。只有一頁用的留在那一頁：共用檔裝滿只有一個地方
 * 用的東西，就不是共用檔了。 */
"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (text) => String(text == null ? "" : text)
  .replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(method, path, body) {
  const response = await fetch(path, {
    method, headers: body ? {"Content-Type": "application/json"} : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `${path} 失敗`);
  return data;
}

function clock(seconds) {
  seconds = Math.max(0, seconds || 0);
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

function leanTag(lean) {
  const which = /left/.test(lean) ? "left" : /right/.test(lean) ? "right"
              : lean === "neutral" ? "neutral" : "";
  return lean ? `<span class="lean ${which}">${escapeHTML(lean)}</span>` : "";
}

/* 留言的兩種存法都要讀得懂：網頁的按鈕存成分組，收集那一輪存成扁平。
   六十則留言曾經因為只認一種而顯示成 0。 */
function voiceCount(pile) {
  return (pile.voices || []).reduce(
    (sum, one) => sum + (Array.isArray(one.comments) ? one.comments.length : 1), 0);
}

/* 一次只有一個長工作在跑（收集、壓片都是分鐘級）。問而不是推，因為一個安靜
   四分鐘的頁面看起來就是壞了。 */
let JOB_TIMER = null;
async function watchJob(whenDone) {
  const tick = async () => {
    let job;
    try { job = await api("GET", "/api/job"); } catch (error) { return; }
    const bar = $("jobBar");
    if (!bar) return;
    bar.hidden = job.state !== "running" && job.state !== "failed";
    $("jobWhat").textContent = job.what || "";
    $("jobNote").textContent = job.note || "";
    $("jobClock").textContent = job.seconds ? `${Math.round(job.seconds)}s` : "";
    $("jobFill").style.width =
      job.steps ? `${(job.step / job.steps) * 100}%` : "0%";
    bar.classList.toggle("failed", job.state === "failed");
    if (job.state === "running") return;
    clearInterval(JOB_TIMER); JOB_TIMER = null;
    if (job.state === "done" && whenDone) whenDone();
  };
  clearInterval(JOB_TIMER);
  await tick();
  JOB_TIMER = setInterval(tick, 1500);
}

/* 三種圖片各補不同的洞，互相不能取代。兩頁都要說明這件事：素材頁在收集時
   說，文案頁在挑圖時說。 */
const WHERE_FROM = {
  stock: "Pexels，可商用免標示。用在抽象的東西——帳單、電表、排隊",
  real: "Wikimedia Commons，多為 CC BY／BY-SA，出處要上畫面。用在有名字的人事地",
  frame: "從這個題目自己的新聞影片剪的。算在 25% 的引用額度裡",
};


/* ---------------------------------------------------------------- 燈箱
 *
 * 兩頁都用：素材頁點圖片架上的一張，文案頁點逐句表上的一格。使用者說過
 * 「太小張了」，而挑圖這件事整個門（unchecked）的意義就在於真的看過。
 *
 * 標記自己注入，這樣兩個 HTML 不用各留一份 —— 那正是拆頁面時最容易產生的
 * 第二份副本。 */

function shotBox() {
  if ($("big")) return;
  const box = document.createElement("div");
  box.id = "big";
  box.hidden = true;
  box.innerHTML = `
    <div class="big-inner">
      <div class="big-bar">
        <b id="bigWho"></b><span id="bigWhat"></span>
        <button id="bigClose" class="tiny">關閉</button>
      </div>
      <div id="bigStage"></div>
      <div id="bigNote"></div>
    </div>`;
  document.body.appendChild(box);
  $("bigClose").onclick = closeShot;
  box.addEventListener("click", (event) => {
    if (event.target.id === "big") closeShot();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !box.hidden) closeShot();
  });
}

function openShot(shot) {
  shotBox();
  const stage = $("bigStage");
  $("bigWho").textContent = shot.show || shot.say || "";
  $("bigWhat").textContent = shot.say || "";
  if (shot.clip) {
    /* `shot.clip` 現在是**剪好的那個檔案**，不是原片加 #t=起,訖 —— 剪好的
       檔案本身就只有這一句的長度，不用再叫瀏覽器自己跳過去。 */
    stage.innerHTML = `<video controls autoplay muted playsinline
      src="${shot.clip}"></video>`;
  } else {
    stage.innerHTML = `<img src="${shot.card || shot.pic}" alt="">`;
  }
  const notes = [];
  if (shot.said) notes.push(`段落逐字稿 <em>${escapeHTML(shot.said)}</em>`);
  if (shot.caption) notes.push(`圖說 <em>${escapeHTML(shot.caption)}</em>`);
  if (shot.credit) notes.push(escapeHTML(shot.credit));
  if (shot.from) notes.push(`出處 ${escapeHTML(shot.from)}`);
  if (shot.card) notes.push("自製卡片");
  $("bigNote").innerHTML = notes.join("　·　");
  $("big").hidden = false;
}

function closeShot() {
  const stage = $("bigStage");
  if (stage) stage.innerHTML = "";     // stops a clip that is still playing
  if ($("big")) $("big").hidden = true;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", shotBox);
} else {
  shotBox();
}


/* ---------------------------------------------------------------- 報錯
 *
 * 一個 JavaScript 例外會讓右半邊停在「左邊選一個」，而伺服器回 200 —— 這個
 * 專案有過一次整頁空白而看起來正常的紀錄，所以壞了要說出來。
 *
 * 但不是每個例外都值得說。點選單離開時，這一頁還在等的 fetch 會被瀏覽器中
 * 止，promise 拒絕，於是錯誤面板在新頁面蓋上來之前閃一下 —— 那個錯誤屬於
 * 你正在離開的那一頁，而且什麼事都沒發生。
 *
 * 所以離開中就閉嘴，中止造成的 fetch 失敗也閉嘴。 */

let LEAVING = false;
for (const when of ["pagehide", "beforeunload"]) {
  window.addEventListener(when, () => { LEAVING = true; });
}
/* 點到同源連結就算開始離開了 —— pagehide 有時候晚於 fetch 被中止。 */
document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href]");
  if (link && link.origin === location.origin && !link.target) LEAVING = true;
}, true);

/* 換頁一律走這裡，不要直接寫 location.href。
 *
 * 一，它會先立旗子，這樣還在等的 fetch 被中止時不會跳出一個屬於舊頁面的錯
 * 誤（上面那個 click 監聽只看得到 <a>，按鈕看不到）。
 *
 * 二，操作用按鈕不用超連結：一個會檢查「有沒有沒存的修改」的動作，可能決定
 * 不讓你走，而超連結承諾的是一定過去。瀏覽器也會給它右鍵選單、中鍵開新視
 * 窗、狀態列網址 —— 全部都是對「連到某處」的承諾，不是對「執行某事」的。 */
function leaveTo(url) {
  LEAVING = true;
  location.href = url;
}

function goneQuiet(error) {
  if (LEAVING) return true;
  const said = String((error && error.message) || error || "");
  return /Failed to fetch|NetworkError|aborted|Load failed/i.test(said);
}

function reportFault(error) {
  if (goneQuiet(error)) return;
  const paper = $("paper");
  if (!paper) { console.error(error); return; }
  paper.innerHTML = `<div class="empty" style="color:var(--warn);text-align:left;
      white-space:pre-wrap;font:12px var(--mono)">頁面出錯了：
${escapeHTML((error && error.stack) || error)}</div>`;
}


/* ---------------------------------------------------------------- 佔位
 *
 * 「左邊選一份文案」那塊字是靜態標記，所以它一定先畫出來，然後才被真的內容
 * 換掉。三個 API 串著跑要六百毫秒，那六百毫秒就是切換頁面時閃的那一下。
 *
 * 兩件事一起做：boot 裡的呼叫改成並行（在別處），還有這裡 —— 慢到一定程度
 * 才顯示佔位，快的時候一次都不畫。180 毫秒是人開始覺得「沒反應」的量級，
 * 比那快就不必解釋自己在做什麼。 */

function holdOff(id = "paper", after = 180) {
  const box = $(id);
  if (!box) return () => {};
  const was = box.innerHTML;
  box.innerHTML = "";
  const timer = setTimeout(() => { box.innerHTML = was; }, after);
  return () => clearTimeout(timer);
}
