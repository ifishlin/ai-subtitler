/* 每一頁頂上的那條導覽列。
 *
 * 一個檔案，所有頁面共用。本來每頁之間完全沒有連結 —— 要換一個服務只能改
 * 網址列，而那表示不知道網址的人就到不了。
 *
 * 只有一份清單，`WHERE`。加一個服務就加一行，不用去十幾個 HTML 裡各補一次。 */

/* 順序照實際的先後，不照當初加進來的先後。短片那一套用得多，放前面；
   長片那三個是 產製 → 導演台 → 組裝（先辨識才有東西可以校對），本來
   排成「導演台 → 文案 → 產製」，跟做事的順序相反。

   後面兩組是收起來的。十四格排成一條的時候，「規定」右邊那八格看起來是
   一團 —— 而分界不是主觀的：

     資料流   三頁都吃 ?name=，都要先選一個題目，而且是同一批資料的三層
              深度（原始 JSON → 哪些活到 prompt → 真正送出去那份）
     設定     三頁都不用選題目，改的都是檔案

   門和文件不屬於任何一組：門是橫跨所有文案的全域表，文件是 md 閱讀器。
   收進下拉只會更難找，所以留在最上層。 */
const WHERE = [
  ["/",          "首頁",   "🏠"],
  ["/topics",    "素材",   "📚"],
  ["/scripts",   "短影音", "🎬"],
  ["/produce",   "產製",   "⚙️"],
  ["/desk",      "導演台", "📽"],
  ["/assemble",  "組裝",   "🧩"],
  {group: "資料流", icon: "🔀", items: [
    // 照資料的方向排，不照當初加進來的順序。
    ["/records",   "紀錄",     "🗄"],
    ["/material",  "素材去哪", "🔀"],
    ["/prompt",    "送去寫",   "✉️"],
  ]},
  {group: "設定", icon: "⚙︎", items: [
    ["/prompts",   "規定",   "📝"],
    ["/houses",    "片型",   "🎞"],
    ["/cards",     "卡片",   "🃏"],
    ["/broll",     "情境影片", "🎥"],
    ["/shots",     "四種鏡頭", "🎬"],
    ["/config",    "所有設定", "🔧"],
  ]},
  ["/gates",     "門",     "🚦"],
  ["/docs",      "文件",   "📓"],
];

/* 換一個看法的時候，題目要跟著走。
 *
 * 這三頁本來就吃 `?name=`，只是從來沒有人傳 —— 所以在紀錄頁看梅西納、
 * 切到送去寫，會跳到伺服器自己挑的「最近動過的題目」，而畫面上看不出
 * 換過。整併如果只是把八格藏起來，那就只是藏起來。
 *
 * 只補這三個路徑。`/docs?name=` 的 name 是文件名，補過去會落到白名單外。 */
const CARRIES = new Set(["/records", "/material", "/prompt"]);

function withName(path) {
  const now = new URLSearchParams(location.search).get("name");
  if (!now || !CARRIES.has(path) || !CARRIES.has(here())) return path;
  return path + "?name=" + encodeURIComponent(now);
}

function here() {
  return location.pathname.replace(/\/$/, "") || "/";
}

function link([path, label, icon]) {
  const on = path === here() ? " class=\"here\"" : "";
  return `<a href="${withName(path)}"${on}>${icon} ${label}</a>`;
}

function navBar() {
  /* 樣式在 theme.css 的 .topnav 底下。本來寫在這裡，就是第八份配色。 */
  const bar = document.createElement("nav");
  bar.className = "topnav";
  bar.innerHTML = WHERE.map((one) => {
    if (Array.isArray(one)) return link(one);
    const inside = one.items.some(([path]) => path === here());
    return `<span class="grp${inside ? " here" : ""}">
      <button type="button" class="top" aria-haspopup="true"
        aria-expanded="false">${one.icon} ${one.group
        }<span class="arrow">▾</span></button>
      <span class="drop">${one.items.map(link).join("")}</span>
    </span>`;
  }).join("") + '<span class="gap"></span><span class="mark">影片流水線</span>';
  document.body.insertBefore(bar, document.body.firstChild);
  wire(bar);
}

/* 滑過就開，點也能開。
 *
 * 只靠 :hover 的話，滑鼠從按鈕斜著移向選單最後一項，中途會離開兩個元素的
 * 範圍，選單就在半路關掉 —— 那個抖動比多按一下更煩。所以離開之後給一段
 * 寬限；而觸控螢幕沒有 hover，那條路只剩點擊。 */
function wire(bar) {
  let closing;
  const shut = (except) => {
    bar.querySelectorAll(".grp.open").forEach((one) => {
      if (one !== except) {
        one.classList.remove("open");
        one.querySelector(".top").setAttribute("aria-expanded", "false");
      }
    });
  };
  bar.querySelectorAll(".grp").forEach((grp) => {
    const button = grp.querySelector(".top");
    const open = (yes) => {
      clearTimeout(closing);
      if (yes) shut(grp);
      grp.classList.toggle("open", yes);
      button.setAttribute("aria-expanded", yes ? "true" : "false");
    };
    grp.addEventListener("mouseenter", () => open(true));
    grp.addEventListener("mouseleave", () => {
      clearTimeout(closing);
      closing = setTimeout(() => open(false), 220);
    });
    button.addEventListener("click", (event) => {
      event.preventDefault();
      open(!grp.classList.contains("open"));
    });
    grp.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { open(false); button.focus(); return; }
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      open(true);
      const items = [...grp.querySelectorAll(".drop a")];
      const at = items.indexOf(document.activeElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      const next = at < 0 ? (step > 0 ? 0 : items.length - 1)
                          : (at + step + items.length) % items.length;
      items[next].focus();
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".topnav .grp")) shut(null);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") shut(null);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", navBar);
} else {
  navBar();
}
