// Synthetic DOM doubles test the shipped script; this is not browser visual QA.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = fs.readFileSync('src/elektro_vienna/review.js', 'utf8');
const context = {format_version:1, view_id:'synthetic-view', case_id:'synthetic-case', baseline_id:'synthetic-baseline'};
const rows = [['item','summary',['correct','partially_correct','unsure']], ['thread','conversation',['belongs','does_not_belong','unsure']], ['message','exception',['belongs','does_not_belong','unsure']]].map(([kind,id,values]) => {
  const select = {value:'', options:['',...values].map(value=>({value}))};
  const textarea = {value:''};
  return {dataset:{kind,id}, select, textarea, querySelector: tag => tag==='select'?select:textarea};
});
const ack = {dataset:{ack:'conversation'},checked:false};
function element(value='') { return {value,textContent:'',handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}}; }
const elements = {result:element(),reviewer:element(),download:element(),resume:element()};
let blob,clicked=0;
const document = {querySelectorAll:s=>s==='.review-control'?rows:[ack],getElementById:id=>elements[id],addEventListener(){},body:{append(){}},createElement(){return {click(){clicked++;},remove(){}};}};
const sandbox = {CONTEXT:context,document,window:{addEventListener(){},confirm:()=>true},Blob,URL:{createObjectURL(b){blob=b;return 'blob:synthetic';},revokeObjectURL(){}},setTimeout(){}};
vm.createContext(sandbox);vm.runInContext(script,sandbox);
const download = () => elements.download.handlers.click();
const resume = async object => elements.resume.handlers.change({target:{value:'file',files:[{size:200,text:async()=>JSON.stringify(object)}]}});
(async()=>{
  download();assert.match(elements.result.textContent,/Namen/);assert.equal(clicked,0);
  elements.reviewer.value='Synthetic Tester';rows[0].textarea.value='Unselected comment';
  download();assert.match(elements.result.textContent,/Kommentar/);assert.equal(clicked,0);
  rows[0].select.value='partially_correct';rows[1].select.value='belongs';
  download();assert.match(elements.result.textContent,/Umfang/);assert.equal(clicked,0);
  ack.checked=true;rows[2].select.value='does_not_belong';rows[2].textarea.value='Other topic';
  download();assert.equal(clicked,1);
  const exported=JSON.parse(await blob.text());
  assert.equal(exported.decisions.length,3);assert.equal(exported.decisions[1].all_shown_messages_acknowledged,true);
  assert.equal(exported.decisions[2].status,'does_not_belong');assert.equal(exported.view_id,context.view_id);
  assert.match(exported.reviewed_at,/Z$/);assert.equal(exported.reviewer,'Synthetic Tester');
  await resume({...exported,view_id:'stale'});assert.match(elements.result.textContent,/anderen Fallansicht/);
  assert.equal(rows[0].textarea.value,'Unselected comment');
  await resume({...exported,decisions:[...exported.decisions,exported.decisions[0]]});assert.match(elements.result.textContent,/doppelte/);
  await resume({...exported,reviewer:'Resumed Tester',decisions:[{kind:'item',id:'summary',status:'unsure',comment:'<script>not executable</script>'}]});
  assert.match(elements.result.textContent,/geladen/);assert.equal(elements.reviewer.value,'Resumed Tester');
  assert.equal(rows[1].select.value,'');assert.equal(ack.checked,false);assert.equal(rows[0].textarea.value,'<script>not executable</script>');
  download();assert.equal(clicked,2);assert.equal(JSON.parse(await blob.text()).decisions.length,1);
  console.log('Review JavaScript: export guards, thread scope, exceptions, stale/duplicate import and resume passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
