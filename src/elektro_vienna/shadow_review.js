"use strict";
(() => {
  const articles = [...document.querySelectorAll("article")];
  const status = document.getElementById("status");
  let dirty = false;
  document.addEventListener("input", () => { dirty = true; });
  window.addEventListener("beforeunload", event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  document.querySelectorAll(".copy").forEach(button => button.addEventListener("click", async () => {
    const draft = button.closest("article").querySelector(".draft");
    try {
      await navigator.clipboard.writeText(draft.textContent);
      status.textContent = "Copied locally. Nothing sent. Check the current conversation before use.";
    } catch (_) {
      const range = document.createRange(); range.selectNodeContents(draft);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      status.textContent = "Clipboard unavailable. Draft selected: press Ctrl+C.";
    }
  }));
  document.getElementById("download").addEventListener("click", () => {
    const feedback = {
      kind: "live_shadow_feedback", format_version: 1, batch: document.body.dataset.batch,
      reviewer: "Giovanni Superti-Furga", recorded_at: new Date().toISOString(),
      items: articles.map(article => ({input_reference: article.dataset.input,
        card_reference: article.dataset.card || null,
        choice: article.querySelector("input:checked")?.value || null,
        comment: article.querySelector("textarea").value}))
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(feedback, null, 2)], {type: "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = "live-shadow-feedback.json";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    dirty = false; status.textContent = "Download started. Preserve the saved JSON with shadow-feedback; this page does not autosave.";
  });
})();
