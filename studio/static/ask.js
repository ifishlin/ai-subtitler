/* The page's own asking and telling, shared by every page in the studio.

   The browser's confirm() and alert() work, and they arrive in the platform's
   chrome saying 127.0.0.1:8000 說 in the middle of a page that took care over
   its own look. More to the point they cannot show what is about to be lost,
   which is the entire question when the prompt is 刪掉？

   Lifted out of scripts.html so four pages share one dialog rather than
   growing four slightly different ones.

   Needs: a $ (id lookup) and an escapeHTML in scope, and ask.css.
*/
/* Asking and telling, in the page's own voice.

   Both return promises so the call sites read the way the native ones did --
   `if (!await confirmed(...)) return;` -- rather than turning every one of
   them inside out into callbacks. */
let ASKING = null;

function showAsk(title, body, yes, no) {
  $("askTitle").textContent = title;
  $("askBody").innerHTML = body;
  $("askYes").textContent = yes;
  $("askNo").hidden = !no;
  if (no) $("askNo").textContent = no;
  $("ask").hidden = false;
  $("askYes").focus();
  return new Promise((settle) => { ASKING = settle; });
}

function closeAsk(answer) {
  // Read before hiding: the field is inside the dialog and asked() reads it
  // from the resolved promise.
  $("ask").hidden = true;
  if (ASKING) { ASKING(answer); ASKING = null; }
}

/* `lose` is what this exists for: the native box cannot show what is about to
   go, and that is the whole of the question. */
function confirmed(title, {body = "", lose = [], yes = "確定", no = "取消"} = {}) {
  const list = lose.length
    ? `<ul class="ask-lose">${lose.map((one) =>
        `<li>${escapeHTML(one)}</li>`).join("")}</ul>` : "";
  return showAsk(title, `${body ? `<p>${escapeHTML(body)}</p>` : ""}${list}`,
                 yes, no);
}

/* The one that takes an answer back. Same shape as the others so a call site
   reads `const name = await asked(...)` and a cancel is null, exactly as
   prompt() behaved. */
function asked(title, {body = "", value = "", placeholder = "",
                       yes = "確定", no = "取消"} = {}) {
  const field = `<input id="askText" value="${escapeHTML(value)}"
    placeholder="${escapeHTML(placeholder)}" spellcheck="false">`;
  const wait = showAsk(title, `${body ? `<p>${escapeHTML(body)}</p>` : ""}${field}`,
                       yes, no);
  const box = $("askText");
  box.focus();
  box.select();
  return wait.then((said) => (said ? box.value.trim() || null : null));
}

function told(title, body = "") {
  /* 離開這一頁的時候不要彈。點選單切換頁面會中止還在等的 fetch，二十個
     catch 於是各自叫一次 told()，而使用者看到的是一個在新頁面蓋上來之前
     閃過的錯誤對話框 —— 那個錯誤屬於他正要離開的那一頁，而且什麼事都沒
     發生過。goneQuiet() 在 shared.js，這裡容許它不存在（有些頁面沒載）。 */
  if (typeof goneQuiet === "function" && goneQuiet(body)) return Promise.resolve(true);
  return showAsk(title, body ? `<p>${escapeHTML(body)}</p>` : "", "好", "");
}

$("askYes").onclick = () => closeAsk(true);
$("askNo").onclick = () => closeAsk(false);
$("ask").addEventListener("click", (event) => {
  if (event.target.id === "ask") closeAsk(false);
});
document.addEventListener("keydown", (event) => {
  if ($("ask").hidden) return;
  if (event.key === "Escape") closeAsk(false);
  if (event.key === "Enter") closeAsk(true);
});

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("ask")) return;
  document.body.insertAdjacentHTML("beforeend", `<!-- The page's own asking and telling. The browser's confirm() and alert()
     work, and they arrive with the wrong voice: a system chrome box in the
     platform's font saying 127.0.0.1:8000 說, in the middle of a page that
     spent some care on its own. They also cannot show what is about to be
     lost, which is the only thing that matters when the question is 刪掉？ -->
<div id="ask" hidden>
  <div class="ask-inner">
    <b id="askTitle"></b>
    <div id="askBody"></div>
    <div class="ask-buttons">
      <button id="askNo" class="tiny">取消</button>
      <button id="askYes" class="tiny primary">確定</button>
    </div>
  </div>
</div>`);
  $("askYes").onclick = () => closeAsk(true);
  $("askNo").onclick = () => closeAsk(false);
  $("ask").addEventListener("click", (event) => {
    if (event.target.id === "ask") closeAsk(false);
  });
  document.addEventListener("keydown", (event) => {
    if ($("ask").hidden) return;
    if (event.key === "Escape") closeAsk(false);
    if (event.key === "Enter") closeAsk(true);
  });
});
