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
  const style = document.createElement("style");
  style.textContent = `
    .topnav{display:flex;gap:2px;align-items:center;flex-wrap:wrap;
      padding:7px 12px;background:#0b0f14;border-bottom:1px solid #1d2836;
      font:13px -apple-system,"PingFang TC",sans-serif;
      position:sticky;top:0;z-index:9000}
    .topnav a{color:#7f93a6;text-decoration:none;padding:5px 11px;
      border-radius:7px;white-space:nowrap}
    .topnav a:hover{color:#e8eef4;background:#141c26}
    .topnav a.here{color:#0b0f14;background:#5aa9e6;font-weight:600}
    .topnav .gap{flex:1}
    .topnav .mark{color:#4a5a6a;font:11px ui-monospace,monospace;
      padding-right:6px}
    @media (prefers-color-scheme:light){
      .topnav{background:#f4f6f8;border-bottom-color:#dde3e9}
      .topnav a{color:#5a6b7c}
      .topnav a:hover{color:#16232e;background:#e6ebf0}
    }`;
  document.head.appendChild(style);

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
