import { api } from './api.js';
import { ImportWorkspace } from './imports.js';
import { esc, icon, operationsSummary, crewStrip, problemQueue, pagination, crewWork, problemDetail, sourceReports } from './components.js';
const state = {mode:'maya',crew:'',overview:null,page:null,detail:null,filters:{status:'open',priority:'',q:'',sort:'priority',page:1},evidencePage:1};
let refreshRunning=0, requestVersion=0, detailVersion=0, busy=false, lastFocus=null, searchTimer, toastTimer;
const mutationKeys = new Map();
const $ = selector => document.querySelector(selector);
$('#app').innerHTML = `<header class="app-header"><a href="/" class="brand"><span class="brand-mark">${icon('grid',21)}</span><span>Works Dispatch<small>MUTHUWELLA MUNICIPAL COUNCIL</small></span></a><nav aria-label="Workspace"><button class="view-tab active" data-action="maya">Coordinator</button><button class="view-tab" data-action="crew-mode">Crew view</button></nav><div class="identity"><span class="avatar">M</span><span>Maya<small>Works Coordinator</small></span></div></header><main><div class="page-heading"><div><div class="eyebrow">PUBLIC WORKS / OPERATIONS</div><h1 id="page-title">Morning dispatch.</h1><p id="page-subtitle">From resident reports to work that matters.</p></div><div class="session-meta"><span class="live-indicator"></span><span id="sync-status" role="status">Connecting to workspace</span><button class="button secondary" data-action="refresh">Refresh ${icon('clock',14)}</button></div></div><div id="imports"></div><div id="top-summary"><div class="loading-block" role="status">Loading the morning picture…</div></div><div id="crew-strip"></div><div class="analysis-notice" id="analysis-notice"></div><section class="queue-section"><div class="queue-heading"><div><h2 id="queue-title">Problem queue <span id="queue-count" class="count">—</span></h2><p id="queue-caption">Review the reasons. Make the call. Dispatch with confidence.</p></div><span class="small-label" id="sort-explanation">Operational priority · highest first</span></div><div class="filter-bar"><label class="search-label">${icon('search',17)}<input id="search" type="search" placeholder="Search problem, road or work type" aria-label="Search problems"></label><label class="filter-label">Status<select id="status"><option value="open">All open</option><option value="unreviewed">Unreviewed</option><option value="assigned">Assigned</option><option value="in_progress">In progress</option><option value="completed">Completed</option><option value="all">All statuses</option></select></label><label class="filter-label">Priority<select id="priority"><option value="">All priorities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label><label class="filter-label" id="sort-filter">Sort<select id="sort"><option value="priority">Operational priority</option><option value="reports">Most reports</option><option value="oldest">Oldest first</option></select></label><label class="filter-label" id="crew-filter" hidden>Crew<select id="crew-select"></select></label></div><div id="queue" aria-live="polite"><div class="loading-block" role="status">Loading problems…</div></div><div id="pagination"></div></section><footer class="page-footer"><span>Local operations workspace <b>·</b> Decisions stay with Maya</span><span>Demo role switch · no authentication</span></footer></main><dialog id="detail-dialog" aria-labelledby="detail-title"><div id="detail-panel"></div></dialog>`;
const imports = new ImportWorkspace($('#imports'), () => refresh());
imports.connect();
function toast(message) { $('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),4500); }
function filters() { return {...state.filters, ...(state.mode==='crew'?{crew:state.crew}:{} )}; }
function renderTop() {
 const o=state.overview;$('#top-summary').innerHTML=operationsSummary(o);$('#crew-strip').innerHTML=crewStrip(o.crews);
 $('#analysis-notice').innerHTML=`<span>${icon('reports',16)} <strong>${o.sourceReportCount} supporting reports → ${o.distinctProblems} problems.</strong> ${esc(o.analysisSummary || 'Review evidence and recommendations before dispatch.')}</span>`;
 const select=$('#crew-select');
 select.innerHTML=o.crews.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
 if(!o.crews.some(c=>c.id===state.crew))state.crew=o.crews[0]?.id || '';
 select.value=state.crew;select.disabled=!o.crews.length;
}
async function refresh({silent=false}={}) {
 if(silent && refreshRunning)return;
 refreshRunning++;
 const version=++requestVersion;
 if(!silent){$('#queue').setAttribute('aria-busy','true');$('#sync-status').textContent='Updating workspace…';}
 try {
  // Crews are needed before the initial crew-filtered query.
  const overview=await api.overview();if(version!==requestVersion)return;
  state.overview=overview;renderTop();
  const page=state.mode==='crew' && !state.crew ? {items:[],total:0,page:1,pageSize:15} : await api.problems(filters());if(version!==requestVersion)return;
  state.page=page;
  $('#queue').innerHTML=state.mode==='crew' && !state.crew ? '<div class="empty-state"><h3>No crews configured</h3><p>The backend must provide the council’s crews and capabilities.</p></div>' : state.mode==='maya'?problemQueue(page,overview.crews):crewWork(page,overview.crews,state.crew);
  $('#pagination').innerHTML=pagination(page);$('#queue-count').textContent=page.total;
  $('#sync-status').textContent='Shared workspace · up to date';$('.live-indicator').classList.remove('offline');
  const changed=state.detail && page.items.find(p=>p.id===state.detail.id && p.revision!==state.detail.revision);
  if(changed && $('#panel-error'))$('#panel-error').innerHTML='<div class="inline-error">This record changed in another view. Your draft is preserved. Refresh details before saving.<button class="button secondary" data-action="reload-detail">Load latest record</button></div>';
 } catch(error) {
  if(version!==requestVersion)return;
  $('#sync-status').textContent='Connection interrupted';$('.live-indicator').classList.add('offline');
  $('#queue').innerHTML=`<div class="error-state" role="alert">${icon('warn',28)}<h3>Backend not connected</h3><p>${esc(error.message)}</p><p>No problem data is available until the backend responds.</p><button class="button primary" data-action="refresh">Reconnect</button></div>`;
  $('#pagination').innerHTML='';
  state.overview=null;$('#top-summary').innerHTML='<div class="loading-block">Operational counts unavailable — awaiting the backend.</div>';$('#crew-strip').innerHTML='';$('#analysis-notice').innerHTML='';$('#queue-count').textContent='—';$('#crew-select').disabled=true;
 } finally { refreshRunning--; if(version===requestVersion)$('#queue').removeAttribute('aria-busy'); }
}
function changeMode(mode,crew) {
 if(busy)return;
 state.mode=mode;document.body.dataset.mode=mode;if(crew)state.crew=crew;state.filters={status:'open',priority:'',q:'',sort:'priority',page:1};
 $('#status').value='open';$('#priority').value='';$('#search').value='';$('#sort').value='priority';
 $('#page-title').textContent=mode==='maya'?'Morning dispatch.':'Ready for the field.';
 $('#page-subtitle').textContent=mode==='maya'?'From resident reports to work that matters.':'Your assigned work. One shared picture.';
 $('#queue-title').innerHTML=`${mode==='maya'?'Problem queue':'Assigned work'} <span id="queue-count" class="count">—</span>`;
 $('#queue-caption').textContent=mode==='maya'?'Review the reasons. Make the call. Dispatch with confidence.':'Start a job when you arrive. Keep Maya informed as work progresses.';
 $('#crew-filter').hidden=mode!=='crew';$('#sort-filter').hidden=mode==='crew';
 $('.identity').innerHTML=mode==='maya'?'<span class="avatar">M</span><span>Maya<small>Works Coordinator</small></span>':'<span class="avatar">C</span><span>Crew workspace<small>Shared dispatch board</small></span>';
 document.querySelectorAll('.view-tab').forEach(b=>b.classList.toggle('active',(b.dataset.action==='maya')===(mode==='maya')));
 refresh();
}
async function openDetail(id) {
 const version=++detailVersion;state.detail=null;state.evidencePage=1;
 if(!$('#detail-dialog').open){lastFocus=document.activeElement;$('#detail-dialog').showModal();}
 $('#detail-panel').innerHTML=`<div class="detail-header"><h2 id="detail-title">Loading problem…</h2><button class="icon-button" data-action="close" aria-label="Close details">${icon('close')}</button></div><div class="loading-block" role="status">Loading evidence and decisions…</div>`;
 try {const p=await api.detail(id);if(version!==detailVersion)return;state.detail=p;$('#detail-panel').innerHTML=problemDetail(p,state.overview?.crews || [],state.mode);$('#detail-panel [data-action="close"]').focus();}
 catch(e){if(version!==detailVersion)return;$('#detail-panel').innerHTML=`<div class="error-state"><h2 id="detail-title">Problem unavailable</h2><p>${esc(e.message)}</p><button class="button secondary" data-action="detail" data-id="${esc(id)}">Retry</button><button class="button subtle" data-action="close">Close</button></div>`;}
}
function closeDetail() { if(busy)return;detailVersion++;$('#detail-dialog').close();state.detail=null;lastFocus?.focus(); }
async function evidence(page=1) {
 const id=state.detail?.id;if(!id)return;
 const el=$('#evidence');el.hidden=false;el.innerHTML='<p role="status">Loading source reports…</p>';
 const toggle=$('.evidence-toggle');toggle.setAttribute('aria-expanded','true');toggle.querySelector('.push-right').textContent='Hide ↑';
 try{const data=await api.reports(id,page);if(state.detail?.id!==id)return;state.evidencePage=page;el.innerHTML=sourceReports(data);}
 catch(e){if(state.detail?.id===id)el.innerHTML=`<p class="inline-error">${esc(e.message)}</p><button class="button secondary" data-action="evidence-retry">Retry reports</button>`;}
}
async function mutate(id,action,data) {
 if(busy)return;busy=true;
 const controls=[...document.querySelectorAll('#detail-panel button, #detail-panel input, #detail-panel select, [data-action="transition"]')];const disabled=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);
 try {
  const operation=JSON.stringify([id,action,data]);
  if(!mutationKeys.has(operation))mutationKeys.set(operation,crypto.randomUUID());
  const updated=await api.mutate(id,action,data,mutationKeys.get(operation));
  mutationKeys.delete(operation);
  toast(action==='assign'?'Assignment saved. The crew worklist is updated.':action==='decision'?'Priority decision saved. The queue has been reordered.':data.status==='completed'?'Work completed. Maya’s workspace is updated.':'Work started. Maya can see your progress.');
  if(state.detail?.id===id){state.detail=updated;$('#detail-panel').innerHTML=problemDetail(updated,state.overview?.crews || [],state.mode);}
  await refresh({silent:true});
 }catch(e){
  if($('#panel-error')){$('#panel-error').innerHTML=`<div class="inline-error">${esc(e.message)}${e.status===409?'<button class="button secondary" data-action="reload-detail">Load latest record</button>':''}</div>`;$('#panel-error').scrollIntoView({block:'nearest'});}
  else toast(e.message);
 }finally{busy=false;controls.forEach((c,i)=>c.disabled=disabled[i]);}
}
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-action]');if(!button || button.disabled)return;
 const a=button.dataset.action;
 if(a==='refresh'){refresh();imports.connect();}
 else if(a==='maya')changeMode('maya');
 else if(a==='crew-mode'||a==='crew')changeMode('crew',button.dataset.id);
 else if(a==='detail')openDetail(button.dataset.id);
 else if(a==='close')closeDetail();
 else if(a==='reload-detail' && state.detail)openDetail(state.detail.id);
 else if(a==='reset')changeMode(state.mode);
 else if(a==='prev'||a==='next'){state.filters.page+=a==='prev'?-1:1;refresh();}
 else if(a==='evidence'){$('#evidence').hidden?evidence():($('#evidence').hidden=true,button.setAttribute('aria-expanded','false'),button.querySelector('.push-right').textContent='Show ↓');}
 else if(a==='evidence-retry')evidence(state.evidencePage);
 else if(a==='evidence-prev'||a==='evidence-next')evidence(state.evidencePage+(a==='evidence-prev'?-1:1));
 else if(a==='transition')mutate(button.dataset.id,'status',{revision:Number(button.dataset.revision),status:button.dataset.status,crewId:button.dataset.crew});
});
document.addEventListener('submit',e=>{
 if(!['priority-form','assignment-form'].includes(e.target.id))return;e.preventDefault();if(!state.detail)return;
 const form=new FormData(e.target);const body=Object.fromEntries(form);body.revision=state.detail.revision;
 if(e.target.id==='assignment-form')body.reviewConfirmed=form.has('reviewConfirmed');
 mutate(state.detail.id,e.target.id==='priority-form'?'decision':'assign',body);
});
for(const key of ['status','priority','sort'])$('#'+key).addEventListener('change',e=>{state.filters[key]=e.target.value;state.filters.page=1;if(key==='sort')$('#sort-explanation').textContent=e.target.value==='priority'?'Operational priority · highest first':e.target.value==='reports'?'Report volume first · not a measure of severity':'Earliest valid report first';refresh();});
$('#search').addEventListener('input',e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.filters.q=e.target.value;state.filters.page=1;refresh();},250);});
$('#crew-select').addEventListener('change',e=>{state.crew=e.target.value;state.filters.page=1;refresh();});
$('#detail-dialog').addEventListener('cancel',e=>{e.preventDefault();closeDetail();});
$('#detail-dialog').addEventListener('click',e=>{if(e.target===$('#detail-dialog'))closeDetail();});
setInterval(()=>{if(!document.hidden && !busy && state.overview)refresh({silent:true});},8000);
window.addEventListener('focus',()=>{if(!busy)refresh({silent:true});});
refresh();
