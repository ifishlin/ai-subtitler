/* 每一頁頂上的那條導覽列。
 *
 * 一個檔案，四個頁面共用。本來每頁之間完全沒有連結 —— 要換一個服務只能改
 * 網址列，而那表示不知道網址的人就到不了。
 *
 * 只有一份清單，`WHERE`。加一個服務就加一行，不用去四個 HTML 裡各補一次。 */

/* 順序照實際的先後，不照當初加進來的先後。短片那一套用得多，放前面；
   長片那三個是 產製 → 導演台 → 組裝（先辨識才有東西可以校對），本來
   排成「導演台 → 文案 → 產製」，跟做事的順序相反。 */
const WHERE = [
  ["/",          "首頁",   "🏠"],
  ["/topics",    "素材",   "📚"],
  ["/scripts",   "短影音", "🎬"],
  ["/produce",   "產製",   "⚙️"],
  ["/desk",      "導演台", "📽"],
  ["/assemble",  "組裝",   "🧩"],
  ["/prompts",   "規定",   "📝"],
  ["/gates",     "門",     "🚦"],
  ["/material",  "素材去哪", "🔀"],
  ["/prompt",    "送去寫", "✉️"],
  ["/docs",      "文件",   "📓"],
];

function navBar() {
  const here = location.pathname.replace(/\/$/, "") || "/";
  /* 樣式在 theme.css 的 .topnav 底下。本來寫在這裡，就是第八份配色。 */

  const bar = document.createElement("nav");
  bar.className = "topnav";
  bar.innerHTML = WHERE.map(([path, label, icon]) =>
    `<a href="${path}" class="${path === here ? "here" : ""}">${icon} ${label}</a>`
  ).join("") + '<span class="gap"></span><span class="mark">影片流水線</span>';
  document.body.insertBefore(bar, document.body.firstChild);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", navBar);
} else {
  navBar();
}
