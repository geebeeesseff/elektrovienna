"use strict";
const cards = Array.from(document.querySelectorAll("article[data-id]"));
const selections = () => cards.map(card => ({
  example_id: card.dataset.id,
  choice: card.querySelector("input:checked")?.value ?? null,
  comment: card.querySelector("textarea").value
}));
let unsaved = false;
function update() {
  const count = selections().filter(item => item.choice !== null).length;
  document.getElementById("progress").textContent = `${count} / ${cards.length} bewertet`;
}
document.addEventListener("input", () => {
  unsaved = true;
  document.getElementById("saved").textContent = "Noch nicht heruntergeladen.";
  update();
});
document.getElementById("download").addEventListener("click", () => {
  const cases = selections();
  const review = {
    kind: document.body.dataset.mode === "validation" ? "first_response_validation_review" : "first_response_calibration_review", format_version: 1,
    set: document.body.dataset.set, reviewer: "Giovanni Superti-Furga",
    recorded_at: new Date().toISOString(),
    complete: cases.every(item => item.choice !== null), cases
  };
  if (document.body.dataset.mode === "validation") review.answers_sha256 = document.body.dataset.answers;
  const url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = document.body.dataset.mode === "validation" ? "erstantwort-validierung-bewertung.json" : "erstantwort-kalibrierung-bewertung.json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  unsaved = false;
  document.getElementById("saved").textContent = "Download gestartet. Bitte die Datei aufbewahren.";
});
window.addEventListener("beforeunload", event => {
  if (unsaved) { event.preventDefault(); event.returnValue = ""; }
});
update();
