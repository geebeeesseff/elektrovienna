"use strict";
const controls = Array.from(document.querySelectorAll(".review-control"));
const result = document.getElementById("result");
let dirty = false;
document.addEventListener("input", () => { dirty = true; });
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
function getReview() {
  if (CONTEXT.live && window.reviewDraft) return {format_version:1,kind:"local_review_backup",view_id:CONTEXT.view_id,case_id:CONTEXT.case_id,baseline_id:CONTEXT.baseline_id,reviewed_at:new Date().toISOString(),local_draft:window.reviewDraft()};
  const reviewer = document.getElementById("reviewer").value.trim();
  if (!reviewer) throw new Error("Bitte deinen Namen eintragen.");
  const decisions = [];
  for (const element of controls) {
    const status = element.querySelector("select").value;
    const comment = element.querySelector("textarea").value.trim();
    if (!status && comment) throw new Error("Bitte bei jedem Kommentar eine Bewertung auswählen, z. B. Unsicher.");
    if (!status) continue;
    const d = {kind: element.dataset.kind, id: element.dataset.id, status, comment};
    if (d.kind === "thread") {
      d.all_shown_messages_acknowledged = Array.from(document.querySelectorAll("[data-ack]")).find(e => e.dataset.ack === d.id).checked;
      if (status !== "unsure" && !d.all_shown_messages_acknowledged) throw new Error("Bitte den Umfang der Gesprächsbewertung bestätigen oder einzelne Nachrichten bewerten.");
    }
    decisions.push(d);
  }
  if (!decisions.length) throw new Error("Bitte mindestens einen Eintrag bewerten.");
  return {format_version:CONTEXT.format_version, view_id:CONTEXT.view_id, case_id:CONTEXT.case_id, baseline_id:CONTEXT.baseline_id, reviewer, reviewed_at: new Date().toISOString(), decisions};
}
document.getElementById("download").addEventListener("click", () => {
  try {
    const review = getReview();
    const url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2) + "\n"], {type: "application/json"}));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "fallreview-" + CONTEXT.view_id.slice(0, 12) + "-" + review.reviewed_at.replace(/[:.]/g, "-") + ".json";
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    result.textContent = CONTEXT.live ? "Optionale Sicherung heruntergeladen. Kommentare werden weiterhin automatisch lokal gespeichert." : "Download gestartet. Bitte die Datei aufbewahren; dauerhaft archiviert wird sie beim lokalen Import. Weitere Änderungen als neue Datei herunterladen.";
    if (!CONTEXT.live) dirty = false;
  } catch (error) { result.textContent = error.message; }
});
document.getElementById("resume").addEventListener("change", async event => {
  try {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) throw new Error("Review-Datei ist zu groß.");
    const review = JSON.parse(await file.text());
    if (Object.entries(CONTEXT).filter(([k]) => k !== "live").some(([k,v]) => review[k] !== v)) throw new Error("Die Datei gehört zu einer anderen Fallansicht oder Version.");
    if (CONTEXT.live && review.kind === "local_review_backup" && window.restoreReviewDraft) {
      window.restoreReviewDraft(review.local_draft);
      result.textContent = "Kommentare als Entwurf geladen. Ein Abschluss muss ausdrücklich neu bestätigt werden.";
      return;
    }
    if (CONTEXT.live && window.restoreReviewDraft && Array.isArray(review.decisions)) {
      window.restoreReviewDraft({reviewer:review.reviewer,fields:review.decisions.map(d=>({kind:d.kind,id:d.id,status:d.status,comment:d.comment,ack:!!d.all_shown_messages_acknowledged}))});
      result.textContent = "Frühere Kommentare als Entwurf geladen; alte Bewertungen gelten nicht als neue Entscheidungen.";
      return;
    }
    if (typeof review.reviewer !== "string" || !review.reviewer.trim() || !Array.isArray(review.decisions) || !review.decisions.length || typeof review.reviewed_at !== "string" || !Number.isFinite(Date.parse(review.reviewed_at))) throw new Error("Ungültige Review-Datei.");
    const seen = new Set();
    const validated = review.decisions.map(d => {
      const control = controls.find(c => c.dataset.kind === d.kind && c.dataset.id === d.id);
      const key = d.kind + ":" + d.id;
      if (!control || seen.has(key) || typeof d.comment !== "string" || !d.status || !Array.from(control.querySelector("select").options).some(o => o.value === d.status)) throw new Error("Unbekannte oder doppelte Bewertung.");
      if (d.kind === "thread" && d.status !== "unsure" && d.all_shown_messages_acknowledged !== true) throw new Error("Gesprächsbewertung ohne Bestätigung des Umfangs.");
      seen.add(key); return [control, d];
    });
    if (dirty && !window.confirm("Ungespeicherte Eingaben durch die geladene Datei ersetzen?")) return;
    controls.forEach(c => { c.querySelector("select").value = ""; c.querySelector("textarea").value = ""; });
    document.querySelectorAll("[data-ack]").forEach(c => { c.checked = false; });
    for (const [control, d] of validated) {
      control.querySelector("select").value = d.status;
      control.querySelector("textarea").value = d.comment;
      if (d.kind === "thread") Array.from(document.querySelectorAll("[data-ack]")).find(e => e.dataset.ack === d.id).checked = d.all_shown_messages_acknowledged === true;
    }
    document.getElementById("reviewer").value = review.reviewer;
    dirty = false;
    result.textContent = "Gespeichertes Review geladen. Änderungen werden beim nächsten Download als neue Fassung gespeichert.";
  } catch (error) { result.textContent = "Laden fehlgeschlagen: " + error.message; }
  event.target.value = "";
});

document.addEventListener("click", event => {
  const link = event.target.closest && event.target.closest("a.source-jump");
  if (!link) return;
  const target = document.getElementById(link.getAttribute("href").slice(1));
  if (!target) return;
  let parent = target.parentElement;
  while (parent) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
});
