/* 每一頁頂上的那條導覽列。
 *
 * 一個檔案，四個頁面共用。本來每頁之間完全沒有連結 —— 要換一個服務只能改
 * 網址列，而那表示不知道網址的人就到不了。
 *
 * 只有一份清單，`WHERE`。加一個服務就加一行，不用去四個 HTML 裡各補一次。 */

const WHERE = [
  ["/",          "首頁",   "🏠"],
  ["/desk",      "導演台", "🎬"],
  ["/scripts",   "題目與文案", "📝"],
  ["/produce",   "產製",   "⚙️"],
  ["/assemble",  "組裝",   "🧩"],
  ["/gates",     "門",     "🚦"],
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
