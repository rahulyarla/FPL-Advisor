/* Public-history comparison: separate data, background job and exact metrics. */
let INTEGRITY={ready:false}, INTEGRITY_OPTIONS=null, integrityTimer=null, integrityBusy=false, integrityOpen=false, integrityPage=0, integrityOptionsAt=0;
const INTEGRITY_PAGE_SIZE=100;
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
    if(integrityBusy){integrityBusy=false;if(j.error)integrityError(j.error);else{INTEGRITY=await request('/api/integrity/result');integrityPage=0;renderIntegrity();$('integrityApplyNote').textContent='';}}
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
    $('integrityApply').disabled=true;INTEGRITY=await request('/api/integrity/recalculate',payload);integrityPage=0;renderIntegrity();$('integrityDetail').innerHTML='';$('integrityApplyNote').textContent='Applied to the loaded history. No new FPL requests were needed.';
  }catch(e){integrityError(e.message);}finally{$('integrityApply').disabled=false;}
}
function integrityNum(value,places=2){return value==null?'—':fmt(value,places)+'%';}
function renderIntegrity(){
  if(!INTEGRITY.ready)return;
  const r=INTEGRITY,cfg=r.settings;
  $('integrityResults').classList.remove('hidden');
  $('integritySummary').textContent=`${r.season} · GW${cfg.start_gw}–${cfg.end_gw} · ${r.pairs.length} separate pairs · ${r.matched_pairs} meet the settings · ${r.incomplete_pairs} have incomplete coverage · ${r.self_pairs_skipped} self-comparisons skipped. History checked ${new Date(r.source_checked_at).toLocaleString()}.`;
  $('integrityApplied').textContent=`Applied settings: ${cfg.metric==='squad'?'15-player squad overlap':cfg.metric==='xi'?'Starting XI overlap':'Combined similarity'} · average at least ${cfg.threshold}% · at least ${cfg.min_matching_week_percent}% of compared weeks individually reach ${cfg.threshold}%. Complete coverage is required to label a pair Threshold met.`;
  const search=$('integritySearch').value.toLowerCase(),only=$('integrityOnlyMatches').checked;
  const all=r.pairs.filter(p=>(!only||p.meets_threshold===true)&&`${p.a} ${p.b} ${p.a_name} ${p.b_name}`.toLowerCase().includes(search));
  integrityPage=Math.min(integrityPage,Math.max(0,Math.ceil(all.length/INTEGRITY_PAGE_SIZE)-1));
  const start=integrityPage*INTEGRITY_PAGE_SIZE;
  $('integrityPairs').innerHTML=all.slice(start,start+INTEGRITY_PAGE_SIZE).map(p=>`<tr><td><b>${esc(p.a_name)}</b><small>List A · ${p.a}</small></td><td><b>${esc(p.b_name)}</b><small>List B · ${p.b}</small></td><td class="number">${integrityNum(p.average)}</td><td class="number">${integrityNum(p.latest)}</td><td>${p.compared_weeks}/${p.requested_weeks} GWs</td><td>${p.weeks_above}/${p.compared_weeks} · ${integrityNum(p.weeks_above_percent)}</td><td class="number">${p.longest_streak}</td><td><span class="tag ${p.meets_threshold?'warn':''}">${esc(p.status)}</span></td><td><button class="tiny secondary" onclick="inspectIntegrityPair(${p.a},${p.b})">Timeline</button></td></tr>`).join('')||'<tr><td colspan="9" class="empty">No pairs match this view. Check the lists, filters and coverage.</td></tr>';
  $('integrityPageInfo').textContent=all.length?`${start+1}–${Math.min(start+INTEGRITY_PAGE_SIZE,all.length)} of ${all.length} pairs`:'0 pairs';
  $('integrityPrev').disabled=integrityPage===0;$('integrityNext').disabled=start+INTEGRITY_PAGE_SIZE>=all.length;
  $('integrityWarnings').textContent=r.warnings.length?`${r.warnings.length} history gaps or read errors. Open coverage details below.`:'All requested public snapshots were available.';
  $('integrityWarningDetails').innerHTML=r.warnings.slice(0,200).map(w=>`<li>Entry ${w.entry}${w.gw==null?'':', GW'+w.gw}: ${esc(w.reason)}</li>`).join('')+(r.warnings.length>200?'<li>Additional gaps are reflected in each pair’s coverage.</li>':'');
}
function pageIntegrity(delta){integrityPage+=delta;renderIntegrity();}
async function inspectIntegrityPair(a,b){
  try{
    const p=await request('/api/integrity/pair',{a,b}),cfg=p.settings;
    const name=id=>id==null?'Unknown':esc(p.players[id]?.name||'Player '+id);
    const list=items=>items?.length?items.map(name).join(', '):'None';
    const changes=m=>m?`In: ${list(m.in)}<br>Out: ${list(m.out)}`:'Unavailable / Free Hit transition';
    const labels=p.weeks.map(w=>'GW'+w.gw);
    $('integrityDetail').innerHTML=`<div class="panel"><div class="paneltitle"><div><h3>${esc(p.a_name)} vs ${esc(p.b_name)}</h3><p class="small muted">${p.a} → ${p.b} · ${p.season} · GW${cfg.start_gw}–${cfg.end_gw}</p></div><span class="tag">${esc(p.status)}</span></div><div class="summarygrid"><div class="metric"><b>${integrityNum(p.metrics.squad)}</b><span>Average 15-player overlap</span></div><div class="metric"><b>${integrityNum(p.metrics.xi)}</b><span>Average starting-XI overlap</span></div><div class="metric"><b>${integrityNum(p.metrics.captain)}</b><span>Same captain, where both known</span></div></div>${svgChart({title:'Public team similarity by gameweek',unit:'%',labels,series:[{name:'15-player overlap',color:'#644198',values:p.weeks.map(w=>w.squad??null)},{name:'Starting XI overlap',color:'#16866e',values:p.weeks.map(w=>w.xi??null)},{name:'Combined similarity',color:'#3d74b7',values:p.weeks.map(w=>w.combined??null)},{name:'Your threshold',color:'#bb536c',dashed:true,values:p.weeks.map(()=>cfg.threshold)}]})}<p class="small muted">Combined = 60% squad overlap + 30% starting XI overlap + 10% captain agreement. Missing weeks break streaks. Bench order and inferred changes are context, and do not enter this score.</p><div class="tablewrap"><table><thead><tr><th>GW</th><th>Squad</th><th>Starting XI</th><th>Combined</th><th>Captains: A / B</th><th>Chips: A / B</th><th>Shared / differing players</th><th>Inferred changes: A / B</th><th>Source</th></tr></thead><tbody>${p.weeks.map(w=>!w.available?`<tr><td>GW${w.gw}</td><td colspan="8">${esc(w.reason)}</td></tr>`:`<tr><td>GW${w.gw}</td><td>${w.shared_squad.length}/15 · ${integrityNum(w.squad)}</td><td>${w.shared_xi.length}/11 · ${integrityNum(w.xi)}</td><td>${integrityNum(w.combined)}</td><td>${name(w.left_captain)} / ${name(w.right_captain)}</td><td>${esc(w.left_chip||'None')} / ${esc(w.right_chip||'None')}</td><td><details><summary>Inspect players</summary><p><b>Shared:</b> ${list(w.shared_squad)}</p><p><b>Only A:</b> ${list(w.left_only)}</p><p><b>Only B:</b> ${list(w.right_only)}</p></details></td><td><details><summary>${integrityNum(w.changes)} matching changes</summary><p><b>A:</b> ${changes(w.left_moves)}</p><p><b>B:</b> ${changes(w.right_moves)}</p></details></td><td><a target="_blank" rel="noreferrer" href="https://fantasy.premierleague.com/entry/${p.a}/event/${w.gw}/">A picks</a> · <a target="_blank" rel="noreferrer" href="https://fantasy.premierleague.com/entry/${p.b}/event/${w.gw}/">B picks</a></td></tr>`).join('')}</tbody></table></div><p class="small muted">${esc(p.note)} Starting XIs use public positions 1–11, not points multipliers or live autosub scores. Inferred changes compare adjacent deadline squads, not private transfer timestamps; Free Hit transitions are excluded.</p></div>`;
    $('integrityDetail').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){integrityError(e.message);}
}
