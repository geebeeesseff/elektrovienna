"use strict";
document.getElementById("download").addEventListener("click", () => {
  const cases = Array.from(document.querySelectorAll("section[data-case]")).map(section => ({
    case_id: section.dataset.case,
    preference: section.querySelector("input:checked")?.value || null,
    comment: section.querySelector("textarea").value
  }));
  const record = {kind: "blinded_response_preference", comparison: document.body.dataset.comparison,
    recorded_at: new Date().toISOString(), cases};
  const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url; link.download = "antwortvergleich-bewertung.json"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  document.getElementById("save-status").textContent = "Download angefordert. Bitte die Datei aufbewahren; keine automatische Speicherung.";
});
