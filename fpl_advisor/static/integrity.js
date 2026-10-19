/* Public-history comparison: separate data, background job and exact metrics. */
let INTEGRITY={ready:false}, INTEGRITY_OPTIONS=null, integrityTimer=null, integrityBusy=false, integrityOpen=false, integrityPage=0, integrityOptionsAt=0;
let integrityView='matrix', integrityRowPage=0, integrityColPage=0, integritySelected='', integrityDetailRequest=0;
const INTEGRITY_PAGE_SIZE=100;
const INTEGRITY_MATRIX_ROWS=12, INTEGRITY_MATRIX_COLS=8;
const INTEGRITY_METRIC_NAMES={squad:'15-player squad overlap',xi:'Starting XI overlap',combined:'Combined similarity'};
function integrityError(message){$('integrityError').textContent=message;$('integrityError').classList.remove('hidden');}
function integrityInputs(){
  return {group_a:$('integrityA').value,group_b:$('integrityB').value,start_gw:Number($('integrityStart').value),end_gw:Number($('integrityEnd').value),metric:$('integrityMetric').value,threshold:Number($('integrityThreshold').value),min_matching_week_percent:Number($('integrityRecurrence').value),refresh:$('integrityRefresh').checked};
}
function syncIntegrityThreshold(value){
  $('integrityThreshold').value=value;$('integrityThresholdNumber').value=value;
  $('integrityThresholdValue').textContent=String(value)+'%';
  $('integrityApplyNote').textContent='Click Apply settings to update the results. A larger period or changed ID lists needs Run checks.';
}
function integritySettingsChanged(){$('integrityApplyNote').textContent='Settings changed. Apply settings to recalculate the loaded history, or Run checks to fetch another period or different IDs.';}
function putIntegritySettings(cfg){
  $('integrityA').value=cfg.group_a.join('\n');$('integrityB').value=cfg.group_b.join('\n');
  $('integrityStart').value=cfg.start_gw;$('integrityEnd').value=cfg.end_gw;$('integrityMetric').value=cfg.metric;
  syncIntegrityThreshold(cfg.threshold);$('integrityRecurrence').value=cfg.min_matching_week_percent||0;$('integrityApplyNote').textContent='';
}
async function openIntegrity(force=false){
  const first=!integrityOpen,priorStart=$('integrityStart').value,priorEnd=$('integrityEnd').value;
  if(integrityOpen&&!force&&Date.now()-integrityOptionsAt<60000)return;
  integrityOpen=true;$('integrityError').classList.add('hidden');
  const results=await Promise.allSettled([request('/api/integrity/options'),request('/api/integrity/result'),request('/api/integrity/status')]);
  if(results[0].status==='fulfilled'){
    INTEGRITY_OPTIONS=results[0].value;const o=INTEGRITY_OPTIONS;
    integrityOptionsAt=Date.now();
    ['integrityStart','integrityEnd'].forEach(id=>$(id).innerHTML=o.events.map(e=>`<option value="${e.id}">GW${e.id}${e.finished?'':' · in progress'}</option>`).join(''));
    $('integrityStart').value=Math.max(1,o.latest_gw-4);$('integrityEnd').value=o.latest_gw;
    if(!first){if([...$('integrityStart').options].some(e=>e.value===priorStart))$('integrityStart').value=priorStart;if([...$('integrityEnd').options].some(e=>e.value===priorEnd))$('integrityEnd').value=priorEnd;}
    $('integritySeason').textContent=`${o.season} · published deadlines through GW${o.latest_gw}`;
  }else{integrityOpen=false;integrityError('Could not load the official published gameweeks. '+results[0].reason.message);}
  if(results[1].status==='fulfilled'&&results[1].value.ready){INTEGRITY=results[1].value;if(first&&INTEGRITY_OPTIONS?.season===INTEGRITY.season)putIntegritySettings(INTEGRITY.settings);renderIntegrity();}
  if(results[2].status==='fulfilled'&&results[2].value.running){integrityBusy=true;pollIntegrity();}
}
async function runIntegrity(){
  $('integrityError').classList.add('hidden');
  $('integrityRun').disabled=true;$('integrityApply').disabled=true;
  try{const payload=integrityInputs();if(!payload.group_a.trim()||!payload.group_b.trim())throw new Error('Enter IDs in both lists.');if(!INTEGRITY_OPTIONS)throw new Error('Load the published gameweeks first.');await request('/api/integrity/start',payload);integrityBusy=true;pollIntegrity();}catch(e){integrityError(e.message);$('integrityRun').disabled=false;$('integrityApply').disabled=false;}
}
async function pollIntegrity(){
  clearTimeout(integrityTimer);
  try{
    const j=await request('/api/integrity/status');$('integrityProgress').textContent=j.message||'';
    $('integrityCancel').disabled=!j.running;
    if(j.running){integrityBusy=true;integrityTimer=setTimeout(pollIntegrity,800);return;}
    if(integrityBusy){integrityBusy=false;if(j.error)integrityError(j.error);else{INTEGRITY=await request('/api/integrity/result');clearIntegrityDetail();integrityFilterChanged();$('integrityApplyNote').textContent='';}}
    $('integrityRun').disabled=false;$('integrityApply').disabled=false;
  }catch(e){integrityBusy=false;integrityError(e.message);$('integrityRun').disabled=false;$('integrityApply').disabled=false;}
}
async function cancelIntegrity(){try{await request('/api/integrity/cancel',{});pollIntegrity();}catch(e){integrityError(e.message);}}
async function applyIntegrity(){
  $('integrityError').classList.add('hidden');
  try{
    if(!INTEGRITY.ready)throw new Error('Run checks first.');
    const payload=integrityInputs(),parse=s=>[...new Set(s.trim().split(/[\s,;]+/).filter(Boolean).map(parseRivalId))];
    if(JSON.stringify(parse(payload.group_a))!==JSON.stringify(INTEGRITY.settings.group_a)||JSON.stringify(parse(payload.group_b))!==JSON.stringify(INTEGRITY.settings.group_b))throw new Error('The entry lists changed. Click Run checks to load those teams.');
    $('integrityApply').disabled=true;INTEGRITY=await request('/api/integrity/recalculate',payload);clearIntegrityDetail();integrityFilterChanged();$('integrityApplyNote').textContent='Applied to the loaded history. No new FPL requests were needed.';
  }catch(e){integrityError(e.message);}finally{$('integrityApply').disabled=false;}
}
function integrityNum(value,places=2){return value==null?'—':fmt(value,places)+'%';}
function clearIntegrityDetail(){
  integrityDetailRequest++;integritySelected='';$('integrityDetail').innerHTML='';$('integrityDetail').setAttribute('aria-busy','false');
}
function integrityFilterChanged(){integrityPage=0;integrityRowPage=0;integrityColPage=0;renderIntegrity();}
function integrityVisualChanged(){integrityRowPage=0;integrityColPage=0;renderIntegrity();}
function setIntegrityView(view){
  integrityView=view==='table'?'table':'matrix';
  $('integrityMatrixView').classList.toggle('hidden',integrityView!=='matrix');$('integrityTableView').classList.toggle('hidden',integrityView!=='table');
  $('integrityMatrixButton').setAttribute('aria-pressed',String(integrityView==='matrix'));$('integrityTableButton').setAttribute('aria-pressed',String(integrityView==='table'));
}
function integrityVisiblePairs(){
  if(!INTEGRITY.ready)return [];
  const search=$('integritySearch').value.toLowerCase().trim(),only=$('integrityOnlyMatches').checked,focus=$('integrityFocus').value;
  return INTEGRITY.pairs.filter(p=>(!only||p.meets_threshold===true)&&(!focus||String(p.a)===focus)&&`${p.a} ${p.b} ${p.a_name} ${p.b_name}`.toLowerCase().includes(search));
}
function integrityValue(pair){const v=pair[$('integrityDisplay').value==='latest'?'latest':'average'];return Number.isFinite(v)?v:null;}
function integrityColor(value){
  // A fixed sequential scale: darker means more overlap, with no guilt semantics.
  if(value==null||!Number.isFinite(value))return {background:'#f0f0f4',color:'#625e70'};
  const stops=[[244,241,250],[204,194,227],[130,108,176],[63,38,110]],t=Math.max(0,Math.min(100,value))/100*3,i=Math.min(2,Math.floor(t)),f=t-i;
  const rgb=stops[i].map((n,k)=>Math.round(n+(stops[i+1][k]-n)*f));
  const luminance=rgb.map(n=>{const c=n/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((sum,c,k)=>sum+c*[.2126,.7152,.0722][k],0);
  return {background:`rgb(${rgb.join(',')})`,color:luminance<.179?'#fff':'#000'};
}
function integrityBar(value,threshold=null){
  const n=value==null?null:Math.max(0,Math.min(100,value)),color=integrityColor(n);
  return `<span class="integrity-bar-track" aria-hidden="true"><span class="integrity-bar-fill" style="width:${n??0}%;background:${color.background}"></span>${threshold==null?'':`<i class="integrity-bar-threshold" style="left:${Math.max(0,Math.min(100,threshold))}%"></i>`}</span>`;
}
function renderIntegrityMatrix(pairs){
  const cfg=INTEGRITY.settings,lookup=new Map(pairs.map(p=>[`${p.a}:${p.b}`,p])),names=new Map();
  INTEGRITY.pairs.forEach(p=>{names.set(p.a,p.a_name);names.set(p.b,p.b_name);});
  const aSet=new Set(pairs.map(p=>p.a)),bSet=new Set(pairs.map(p=>p.b));
  let rows=cfg.group_a.filter(id=>aSet.has(id)),cols=cfg.group_b.filter(id=>bSet.has(id));
  if($('integrityOrder').value==='strongest'){
    const aMax=new Map(),bMax=new Map();
    pairs.forEach(p=>{const v=integrityValue(p);if(v!=null){aMax.set(p.a,Math.max(aMax.get(p.a)??-1,v));bMax.set(p.b,Math.max(bMax.get(p.b)??-1,v));}});
    rows.sort((a,b)=>(aMax.get(b)??-1)-(aMax.get(a)??-1)||a-b);cols.sort((a,b)=>(bMax.get(b)??-1)-(bMax.get(a)??-1)||a-b);
  }
  integrityRowPage=Math.max(0,Math.min(integrityRowPage,Math.ceil(rows.length/INTEGRITY_MATRIX_ROWS)-1));
  integrityColPage=Math.max(0,Math.min(integrityColPage,Math.ceil(cols.length/INTEGRITY_MATRIX_COLS)-1));
  const rowStart=integrityRowPage*INTEGRITY_MATRIX_ROWS,colStart=integrityColPage*INTEGRITY_MATRIX_COLS;
  const visibleRows=rows.slice(rowStart,rowStart+INTEGRITY_MATRIX_ROWS),visibleCols=cols.slice(colStart,colStart+INTEGRITY_MATRIX_COLS);
  const axis=id=>`<span class="integrity-axis-name">${esc(names.get(id)||'Entry '+id)}</span><small>${id}</small>`;
  const display=$('integrityDisplay').value==='latest'?`GW${cfg.end_gw}`:`GW${cfg.start_gw}–${cfg.end_gw} average`;
  $('integrityMatrixMeasure').textContent=`${INTEGRITY_METRIC_NAMES[cfg.metric]} · ${display}`;
  $('integrityMatrix').innerHTML=!pairs.length?'<p class="empty">No pairs match this view. Change your filters or check the entry lists.</p>':`<table class="integrity-matrix"><caption class="visually-hidden">${esc(INTEGRITY_METRIC_NAMES[cfg.metric])}: ${esc(display)}. Rows are List A; columns are List B. Select a percentage for gameweek details.</caption><thead><tr><th scope="col" class="integrity-corner">List A ↓ <span>List B →</span></th>${visibleCols.map(id=>`<th scope="col" title="${esc(names.get(id)||'Entry')} · ${id}">${axis(id)}</th>`).join('')}</tr></thead><tbody>${visibleRows.map(a=>`<tr><th scope="row" title="${esc(names.get(a)||'Entry')} · ${a}">${axis(a)}</th>${visibleCols.map(b=>{
    if(a===b)return '<td class="integrity-cell-empty"><span aria-label="Same entry, comparison skipped">Self<small>Skipped</small></span></td>';
    const p=lookup.get(`${a}:${b}`);if(!p)return '<td class="integrity-cell-empty"><span>Filtered<small>Outside this view</small></span></td>';
    const value=integrityValue(p),color=integrityColor(value),key=`${a}:${b}`,partial=!p.complete;
    const description=`${p.a_name} (${a}) versus ${p.b_name} (${b}). ${display}: ${value==null?'unavailable':integrityNum(value)}. Period average: ${integrityNum(p.average)}. Coverage: ${p.compared_weeks} of ${p.requested_weeks} gameweeks. ${p.status}.`;
    return `<td><button class="integrity-cell${p.meets_threshold===true?' is-match':''}${partial?' is-partial':''}${value==null?' is-missing':''}${integritySelected===key?' is-selected':''}" style="background-color:${color.background};color:${color.color}" data-pair="${key}" onclick="inspectIntegrityPair(${a},${b})" aria-label="${esc(description)}" aria-pressed="${integritySelected===key}" title="${esc(description)}"><b>${value==null?'No data':integrityNum(value)}</b><small>${partial?`${p.compared_weeks}/${p.requested_weeks} GWs · partial`:`${p.requested_weeks} GWs${p.meets_threshold===true?' · ✓':''}`}</small></button></td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
  for(const [prefix,start,total,size] of [['Row',rowStart,rows.length,INTEGRITY_MATRIX_ROWS],['Col',colStart,cols.length,INTEGRITY_MATRIX_COLS]]){
    $('integrity'+prefix+'Info').textContent=total?`${prefix==='Row'?'A':'B'} ${start+1}–${Math.min(start+size,total)} of ${total}`:'No entries';
    $('integrity'+prefix+'Prev').disabled=start===0;$('integrity'+prefix+'Next').disabled=start+size>=total;
  }
}
function pageIntegrityMatrix(axis,delta){
  if(axis==='row')integrityRowPage+=delta;else integrityColPage+=delta;
  renderIntegrityMatrix(integrityVisiblePairs());
}
function renderIntegrityBars(pairs){
  const best=new Map(),cfg=INTEGRITY.settings;
  pairs.forEach(p=>{const v=integrityValue(p),prior=best.get(p.a);if(v!=null&&(!prior||v>integrityValue(prior)))best.set(p.a,p);});
  const ranked=[...best.values()].sort((a,b)=>integrityValue(b)-integrityValue(a)||a.a-b.a);
  const mode=$('integrityDisplay').value==='latest'?`GW${cfg.end_gw}`:'period average';
  $('integrityBarsNote').textContent=`${INTEGRITY_METRIC_NAMES[cfg.metric]} · ${mode} · 0–100% scale. Showing ${Math.min(10,ranked.length)} of ${ranked.length} checked entries with scores in this view. Dashed marker: ${cfg.threshold}% threshold; period rules determine the result.`;
  $('integrityBars').innerHTML=ranked.length?`<div class="integrity-ranked-bars">${ranked.slice(0,10).map(p=>`<button class="integrity-ranked-row${!p.complete?' is-partial':''}" onclick="inspectIntegrityPair(${p.a},${p.b})" aria-label="Inspect ${esc(p.a_name)} (${p.a}) versus ${esc(p.b_name)} (${p.b}), ${integrityNum(integrityValue(p))}. ${esc(p.status)}"><span class="integrity-ranked-name"><b>${esc(p.a_name)}</b><small>${p.a} → ${esc(p.b_name)} · ${p.b}</small></span>${integrityBar(integrityValue(p),cfg.threshold)}<span class="integrity-ranked-value"><b>${integrityNum(integrityValue(p))}</b><small>${!p.complete?'Partial history':p.meets_threshold===true?'Settings met':'Below settings'}</small></span></button>`).join('')}</div>`:'<p class="empty">No comparable scores in this view yet.</p>';
}
function renderIntegrity(){
  if(!INTEGRITY.ready)return;
  const r=INTEGRITY,cfg=r.settings;
  $('integrityResults').classList.remove('hidden');
  $('integritySummary').textContent=`${r.season} · GW${cfg.start_gw}–${cfg.end_gw} · ${r.pairs.length} separate pairs · ${r.matched_pairs} meet the settings · ${r.incomplete_pairs} have incomplete coverage · ${r.self_pairs_skipped} self-comparisons skipped. History checked ${new Date(r.source_checked_at).toLocaleString()}.`;
  $('integrityApplied').textContent=`Applied settings: ${cfg.metric==='squad'?'15-player squad overlap':cfg.metric==='xi'?'Starting XI overlap':'Combined similarity'} · average at least ${cfg.threshold}% · at least ${cfg.min_matching_week_percent}% of compared weeks individually reach ${cfg.threshold}%. Complete coverage is required to label a pair Threshold met.`;
  const previousFocus=$('integrityFocus').value,aNames=new Map(r.pairs.map(p=>[p.a,p.a_name]));
  $('integrityFocus').innerHTML='<option value="">All checked entries</option>'+cfg.group_a.map(id=>`<option value="${id}">${esc(aNames.get(id)||'Entry')} · ${id}</option>`).join('');
  if(cfg.group_a.some(id=>String(id)===previousFocus))$('integrityFocus').value=previousFocus;
  const all=integrityVisiblePairs(),highest=all.reduce((best,p)=>p.average!=null&&(!best||p.average>best.average)?p:best,null);
  const stats=[['Pairs in view',all.length,`${r.pairs.length} loaded comparisons`],['Meet period settings',all.filter(p=>p.meets_threshold===true).length,'Complete history required'],['Partial history',all.filter(p=>!p.complete).length,'Review coverage before comparing'],['Highest average',highest?integrityNum(highest.average):'—',highest?`${highest.a_name} ↔ ${highest.b_name}${highest.complete?'':' · partial'}`:'No comparable scores']];
  $('integrityStats').innerHTML=stats.map(([label,value,note])=>`<div class="integrity-stat"><span>${label}</span><b>${esc(value)}</b><small>${esc(note)}</small></div>`).join('');
  renderIntegrityMatrix(all);renderIntegrityBars(all);setIntegrityView(integrityView);
  integrityPage=Math.min(integrityPage,Math.max(0,Math.ceil(all.length/INTEGRITY_PAGE_SIZE)-1));
  const start=integrityPage*INTEGRITY_PAGE_SIZE;
  $('integrityPairs').innerHTML=all.slice(start,start+INTEGRITY_PAGE_SIZE).map(p=>`<tr><td><b>${esc(p.a_name)}</b><small>List A · ${p.a}</small></td><td><b>${esc(p.b_name)}</b><small>List B · ${p.b}</small></td><td class="number integrity-score-number">${integrityNum(p.average)}${integrityBar(p.average)}</td><td class="number">${integrityNum(p.latest)}</td><td>${p.compared_weeks}/${p.requested_weeks} GWs</td><td>${p.weeks_above}/${p.compared_weeks} · ${integrityNum(p.weeks_above_percent)}</td><td class="number">${p.longest_streak}</td><td><span class="tag ${p.meets_threshold?'warn':''}">${esc(p.status)}</span></td><td><button class="tiny secondary" onclick="inspectIntegrityPair(${p.a},${p.b})">Timeline</button></td></tr>`).join('')||'<tr><td colspan="9" class="empty">No pairs match this view. Check the lists, filters and coverage.</td></tr>';
  $('integrityPageInfo').textContent=all.length?`${start+1}–${Math.min(start+INTEGRITY_PAGE_SIZE,all.length)} of ${all.length} pairs`:'0 pairs';
  $('integrityPrev').disabled=integrityPage===0;$('integrityNext').disabled=start+INTEGRITY_PAGE_SIZE>=all.length;
  $('integrityWarnings').textContent=r.warnings.length?`${r.warnings.length} history gaps or read errors. Open coverage details below.`:'All requested public snapshots were available.';
  $('integrityWarningDetails').innerHTML=r.warnings.slice(0,200).map(w=>`<li>Entry ${w.entry}${w.gw==null?'':', GW'+w.gw}: ${esc(w.reason)}</li>`).join('')+(r.warnings.length>200?'<li>Additional gaps are reflected in each pair’s coverage.</li>':'');
}
function pageIntegrity(delta){integrityPage+=delta;renderIntegrity();}
async function inspectIntegrityPair(a,b){
  const ticket=++integrityDetailRequest;integritySelected=`${a}:${b}`;
  document.querySelectorAll('#integrityMatrix .integrity-cell').forEach(cell=>{const active=cell.dataset.pair===integritySelected;cell.classList.toggle('is-selected',active);cell.setAttribute('aria-pressed',String(active));});
  $('integrityDetail').setAttribute('aria-busy','true');$('integrityDetail').innerHTML='<div class="panel"><p role="status">Loading pair history…</p></div>';
  try{
    const p=await request('/api/integrity/pair',{a,b}),cfg=p.settings;
    if(ticket!==integrityDetailRequest)return;
    const name=id=>id==null?'Unknown':esc(p.players[id]?.name||'Player '+id);
    const list=items=>items?.length?items.map(name).join(', '):'None';
    const changes=m=>m?`In: ${list(m.in)}<br>Out: ${list(m.out)}`:'Unavailable / Free Hit transition';
    const labels=p.weeks.map(w=>'GW'+w.gw);
    const strip=p.weeks.map(w=>{const value=w[cfg.metric]??null,color=integrityColor(value);return `<div class="integrity-week${value==null?' is-missing':''}" style="background-color:${color.background};color:${color.color}"><small>GW${w.gw}</small><b>${value==null?'No data':integrityNum(value)}</b><small>${value==null?'Unavailable':value+1e-9>=cfg.threshold?'≥ threshold':'< threshold'}</small></div>`;}).join('');
    const breakdown=[['15-player overlap',p.metrics.squad],['Starting XI overlap',p.metrics.xi],['Same captain',p.metrics.captain]].map(([label,value])=>`<div class="integrity-breakdown-row"><span>${label}</span>${integrityBar(value)}<b>${integrityNum(value)}</b></div>`).join('');
    $('integrityDetail').innerHTML=`<div class="panel integrity-pair-detail"><div class="paneltitle"><div><div class="eyebrow">Selected pair</div><h2>${esc(p.a_name)} vs ${esc(p.b_name)}</h2><p class="small muted">List A · ${p.a} / List B · ${p.b} · ${p.season} · GW${cfg.start_gw}–${cfg.end_gw}</p></div><span class="tag">${esc(p.status)}</span></div><div class="integrity-stats"><div class="integrity-stat"><span>Period average</span><b>${integrityNum(p.average)}</b><small>${esc(INTEGRITY_METRIC_NAMES[cfg.metric])}</small></div><div class="integrity-stat"><span>Latest · GW${cfg.end_gw}</span><b>${integrityNum(p.latest)}</b><small>Last selected gameweek</small></div><div class="integrity-stat"><span>Comparable weeks</span><b>${p.compared_weeks}/${p.requested_weeks}</b><small>${p.complete?'Complete history':'Partial history'}</small></div><div class="integrity-stat"><span>Longest matching streak</span><b>${p.longest_streak} GWs</b><small>${p.weeks_above} weeks reach ${cfg.threshold}% overall</small></div></div><div class="integrity-detail-grid"><div><h3>Overlap through the gameweeks</h3>${svgChart({title:'Public team similarity by gameweek',unit:'%',labels,domain:[0,100],series:[{name:INTEGRITY_METRIC_NAMES[cfg.metric],color:'#644198',values:p.weeks.map(w=>w[cfg.metric]??null)},{name:'Your threshold',color:'#8b6a29',dashed:true,values:p.weeks.map(()=>cfg.threshold)}]})}</div><div><h3>Period breakdown</h3><div class="integrity-breakdown">${breakdown}</div><p class="small muted">Captain agreement is the share of weeks with the same captain, where both are known. Each measure averages its own available weeks.</p></div></div><h3>Every selected gameweek</h3><div class="integrity-week-strip">${strip}</div><p class="small muted">Combined = 60% squad overlap + 30% starting XI overlap + 10% captain agreement. Missing weeks break streaks. Bench order and inferred changes are context, and do not enter this score.</p><details class="integrity-player-details"><summary>Player-by-player history and source links</summary><div class="tablewrap"><table><thead><tr><th>GW</th><th>Squad</th><th>Starting XI</th><th>Combined</th><th>Captains: A / B</th><th>Chips: A / B</th><th>Shared / differing players</th><th>Inferred changes: A / B</th><th>Source</th></tr></thead><tbody>${p.weeks.map(w=>!w.available?`<tr><td>GW${w.gw}</td><td colspan="8">${esc(w.reason)}</td></tr>`:`<tr><td>GW${w.gw}</td><td>${w.shared_squad.length}/15 · ${integrityNum(w.squad)}</td><td>${w.shared_xi.length}/11 · ${integrityNum(w.xi)}</td><td>${integrityNum(w.combined)}</td><td>${name(w.left_captain)} / ${name(w.right_captain)}</td><td>${esc(w.left_chip||'None')} / ${esc(w.right_chip||'None')}</td><td><details><summary>Inspect players</summary><p><b>Shared:</b> ${list(w.shared_squad)}</p><p><b>Only A:</b> ${list(w.left_only)}</p><p><b>Only B:</b> ${list(w.right_only)}</p></details></td><td><details><summary>${integrityNum(w.changes)} matching changes</summary><p><b>A:</b> ${changes(w.left_moves)}</p><p><b>B:</b> ${changes(w.right_moves)}</p></details></td><td><a target="_blank" rel="noreferrer" href="https://fantasy.premierleague.com/entry/${p.a}/event/${w.gw}/">A picks</a> · <a target="_blank" rel="noreferrer" href="https://fantasy.premierleague.com/entry/${p.b}/event/${w.gw}/">B picks</a></td></tr>`).join('')}</tbody></table></div></details><p class="small muted">${esc(p.note)} Starting XIs use public positions 1–11, not points multipliers or live autosub scores. Inferred changes compare adjacent deadline squads, not private transfer timestamps; Free Hit transitions are excluded.</p></div>`;
    $('integrityDetail').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){if(ticket===integrityDetailRequest){integrityError(e.message);$('integrityDetail').innerHTML='<div class="panel"><p class="empty">Could not load this pair. Select a cell to try again.</p></div>';}}
  finally{if(ticket===integrityDetailRequest)$('integrityDetail').setAttribute('aria-busy','false');}
}
