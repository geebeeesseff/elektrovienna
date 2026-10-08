// Draft autosave is technical state. Only the explicit completion action makes decisions.
(() => {
  const name=document.getElementById('live-reviewer'), bottomName=document.getElementById('reviewer');
  const indicator=document.getElementById('save-state'), completionState=document.getElementById('completion-state');
  const whole=document.getElementById('whole-case-ack'), sources=document.getElementById('sources-ack');
  const inputs=Array.from(document.querySelectorAll('input,textarea,select,button'));
  let revision=0, generation=0, timer, ready=false, conflict=false, saving=null, finishing=false, completed=null;
  inputs.forEach(input=>{input.disabled=true;});
  const key=f=>f.kind+':'+f.id;
  function snapshot() {
    return {mode:'correction_comments_v2',reviewer:name.value.trim(),fields:controls.map(c=>({kind:c.dataset.kind,id:c.dataset.id,comment:c.querySelector('textarea').value}))};
  }
  function labels() {
    const states={accepted_by_default:'Im abgeschlossenen Review als richtig angenommen.',correction_requested:'Korrektur im abgeschlossenen Review angefordert.',
      human_confirmed:'Quellenzuordnung im abgeschlossenen Review bestätigt.',human_confirmed_with_exceptions:'Gespräch bestätigt, ausgenommen kommentierte Nachrichten.',unreviewed:'Quellenzuordnung ungeprüft.'};
    for(const c of controls){
      const decision=completed?.decisions.find(d=>key(d)===key(c.dataset));
      c.querySelector('.comment-state').textContent=decision ? states[decision.state] : c.querySelector('textarea').value.trim() ?
        'Korrekturkommentar im Entwurf.' : c.dataset.kind==='item' ? 'Noch keine Annahme – erst nach Abschluss der gesamten Fallprüfung.' : 'Quellenzuordnung noch nicht bestätigt.';
    }
  }
  function validate(review) {
    if(!review || typeof review.reviewer!=='string' || !Array.isArray(review.fields))throw new Error('Ungültige Sicherung.');
    const seen=new Set();
    for(const f of review.fields){
      if(!controls.some(c=>key(c.dataset)===key(f)) || seen.has(key(f)) || typeof f.comment!=='string')throw new Error('Kommentare passen nicht zur Ansicht.');
      seen.add(key(f));
    }
  }
  function restore(review) {
    validate(review);name.value=review.reviewer;bottomName.value=review.reviewer;
    controls.forEach(c=>{c.querySelector('textarea').value='';});
    // Legacy statuses, acknowledgements and replacement prose are never restored as decisions.
    for(const f of review.fields)controls.find(c=>key(c.dataset)===key(f)).querySelector('textarea').value=f.comment;
    whole.checked=false;sources.checked=false;labels();
  }
  function completionLabel(data) {
    const last=data.latest_completed;
    completed=last && last.draft_revision===revision && !dirty ? last : null;
    if(last)completionState.textContent=last.draft_revision===revision ?
      'Fallprüfung abgeschlossen · '+new Date(last.completed_at).toLocaleString('de-AT') :
      'Ein früherer Abschluss bleibt erhalten. Der geänderte Entwurf ist noch nicht abgeschlossen.';
    else completionState.textContent='Noch nicht abgeschlossen.';
    labels();
  }
  async function request(url,body) {
    const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    if(response.status===409){conflict=true;throw new Error('Ein anderes Fenster hat gespeichert. Kommentare als JSON sichern und die Ansicht neu öffnen; nichts wurde überschrieben.');}
    if(!response.ok)throw new Error('Speichern fehlgeschlagen. Eingaben bleiben hier erhalten; bitte erneut versuchen.');
    return data;
  }
  async function save() {
    clearTimeout(timer);
    if(!ready || conflict)return false;
    if(saving)return saving;
    saving=(async()=>{
      try {
        // Also flush any edits arriving during a request before allowing completion.
        do {
          const currentGeneration=generation;
          indicator.textContent='Speichert Entwurf lokal …';
          const data=await request(CONTEXT.live.api,{base_revision:revision,review:snapshot()});
          revision=data.revision;
          if(generation===currentGeneration){dirty=false;completionLabel(data);}
          indicator.textContent='Entwurf gespeichert · '+new Date(data.saved_at).toLocaleTimeString('de-AT');
        } while(dirty && !conflict);
        return !dirty;
      } catch(error){dirty=true;indicator.textContent=error.message;return false;}
      finally{saving=null;}
    })();
    return saving;
  }
  function changed(event) {
    if(!ready || finishing || event.target===whole || event.target===sources || event.target.id==='resume')return;
    if(event.target===bottomName)name.value=bottomName.value;else bottomName.value=name.value;
    generation++;dirty=true;completed=null;whole.checked=false;sources.checked=false;
    labels();completionState.textContent='Geänderter Entwurf – noch nicht abgeschlossen.';
    indicator.textContent='Entwurf noch nicht gespeichert …';clearTimeout(timer);timer=setTimeout(save,900);
  }
  window.reviewDraft=snapshot;
  window.restoreReviewDraft=review=>{
    if(!ready || conflict || finishing)throw new Error('Ansicht noch nicht bereit oder inzwischen geändert.');
    validate(review);
    if(dirty && !window.confirm('Ungespeicherte Kommentare durch die Sicherung ersetzen?'))return;
    completed=null;restore(review);generation++;dirty=true;save();
  };
  document.addEventListener('input',changed);document.addEventListener('change',changed);
  document.getElementById('save-now').addEventListener('click',save);
  document.getElementById('complete-review').addEventListener('click',async()=>{
    if(!ready || conflict || finishing)return;
    if(!name.value.trim()){completionState.textContent='Zum Abschließen bitte deinen Namen eintragen.';return;}
    if(!whole.checked){completionState.textContent='Bitte bestätigen, dass du den gesamten dargestellten Fall geprüft hast.';return;}
    const sourceAck=sources.checked;finishing=true;inputs.forEach(input=>{input.disabled=true;});
    try {
      if(!await save() || dirty)throw new Error('Der Entwurf ist noch nicht gespeichert. Bitte vor dem Abschluss erneut speichern.');
      const done=await request(CONTEXT.live.complete_api,{base_revision:revision,whole_case_acknowledged:true,sources_acknowledged:sourceAck});
      completed=done;labels();
      completionState.textContent='Fallprüfung abgeschlossen · '+new Date(done.completed_at).toLocaleString('de-AT')+'. Kommentare sind für die nächste Agenten-Überarbeitung bereit.';
      indicator.textContent='Entwurf gespeichert · Fallprüfung abgeschlossen.';
    } catch(error){completionState.textContent=error.message;}
    finally{finishing=false;inputs.forEach(input=>{input.disabled=false;});}
  });
  fetch(CONTEXT.live.api).then(async response=>{
    if(!response.ok)throw new Error('Entwurf konnte nicht geladen werden. Bitte Dienst prüfen und die Seite neu öffnen.');
    const data=await response.json();revision=data.revision;restore(data.review);
    ready=true;dirty=false;inputs.forEach(input=>{input.disabled=false;});completionLabel(data);
    indicator.textContent=revision || data.prior_review ? 'Entwurf geladen · noch keine neue fachliche Annahme.' : 'Bereit · Kommentare werden als Entwurf gespeichert.';
  }).catch(error=>{indicator.textContent=error.message;});
})();
