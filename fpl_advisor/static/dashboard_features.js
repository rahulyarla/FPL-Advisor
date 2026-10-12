/* Optional rivals and player analytics. All recommendations retain raw forecasts. */
let RIVALS=[], ANALYTICS={players:{}}, compareRivals=false, ownMeta=null;
const COMPARISONS={}, CHART_COLORS=['#644198','#16866e','#d48527','#3d74b7','#bb536c','#82732e','#387b84','#ab65af'];

function invalidateResults(){
  ['teamResult','wcResult'].forEach(id=>{if($(id))$(id).innerHTML='';delete COMPARISONS[id];});
}
function setRivalComparison(value){
  compareRivals=Boolean(value);
  document.querySelectorAll('.rivalToggle').forEach(el=>el.checked=compareRivals);
  invalidateResults();saveRivalSettings();
}
function saveRivalSettings(){
  $('influenceValue').textContent=$('rivalInfluence').value+'%';
  invalidateResults();
  if(loadedSeason)try{localStorage.setItem('fplAdvisorRivalSettings:'+loadedSeason,JSON.stringify({style:$('rivalStyle').value,influence:$('rivalInfluence').value,loss:$('maxPointsLoss').value}));}catch(e){}
}
function restoreRivalSettings(){
  try{
    const v=JSON.parse(localStorage.getItem('fplAdvisorRivalSettings:'+loadedSeason)||'{}');
    if(['protect','chase'].includes(v.style))$('rivalStyle').value=v.style;
    if(Number.isFinite(Number(v.influence))&&Number(v.influence)>=0&&Number(v.influence)<=15)$('rivalInfluence').value=v.influence;
    if(['0','0.01','0.02'].includes(String(v.loss)))$('maxPointsLoss').value=v.loss;
  }catch(e){}
  $('influenceValue').textContent=$('rivalInfluence').value+'%';
}
async function loadExtras(){
  const outcomes=await Promise.allSettled([request('/api/rivals'),request('/api/analytics')]);
  if(outcomes[0].status==='fulfilled'){RIVALS=outcomes[0].value.rivals;renderRivals();}
  else fail('Rival list could not load: '+outcomes[0].reason.message);
  if(outcomes[1].status==='fulfilled'){
    ANALYTICS=outcomes[1].value;
    populateAnalysisPickers();renderAnalytics();
  }else fail('Player graphs could not load: '+outcomes[1].reason.message);
}
function parseRivalId(value){
  const text=String(value).trim();
  if(/^\d+$/.test(text)&&Number(text)>0)return Number(text);
  try{const u=new URL(text);const match=u.pathname.match(/^\/entry\/(\d+)(?:\/|$)/);if(u.hostname==='fantasy.premierleague.com'&&match&&Number(match[1])>0)return Number(match[1]);}catch(e){}
  throw new Error('Use an entry number or an official FPL /entry/NUMBER/ link: '+text);
}
async function addRivals(){
  $('errorBox').classList.add('hidden');let ids;
  try{ids=[...new Set($('rivalIds').value.split(/[\s,;]+/).filter(Boolean).map(parseRivalId))];if(!ids.length)throw new Error('Enter at least one rival ID.');}catch(e){fail(e.message);return;}
  $('addRivalsBtn').disabled=true;const errors=[];
  try{
    for(let i=0;i<ids.length;i++){
      $('rivalProgress').textContent=`Importing ${i+1} of ${ids.length}…`;
      try{
        const rival=await request('/api/rivals/import',{entry_id:ids[i],own_entry_id:$('entryId').value.trim()||null});
        const at=RIVALS.findIndex(r=>r.entry_id===rival.entry_id);if(at<0)RIVALS.push(rival);else RIVALS[at]=rival;
        renderRivals();
      }catch(e){errors.push(`${ids[i]}: ${e.message}`);}
    }
    $('rivalProgress').textContent=`${ids.length-errors.length} imported; ${errors.length} failed.`;
    if(!errors.length)$('rivalIds').value='';else fail(errors.join(' · '));
    invalidateResults();
  }finally{$('addRivalsBtn').disabled=false;}
}
async function changeRival(id,enabled,remove=false){
  try{const result=await request('/api/rivals/edit',{entry_id:id,enabled,remove});RIVALS=result.rivals;invalidateResults();renderRivals();}catch(e){fail(e.message);renderRivals();}
}
async function refreshRivalTeams(){
  $('refreshRivalsBtn').disabled=true;$('rivalProgress').textContent='Refreshing saved rivals…';
  try{const result=await request('/api/rivals/refresh',{});RIVALS=result.rivals;invalidateResults();renderRivals();$('rivalProgress').textContent=result.errors.length?`${result.errors.length} snapshots could not refresh. Older snapshots are labeled below.`:'Rival snapshots refreshed.';}catch(e){fail(e.message);}finally{$('refreshRivalsBtn').disabled=false;}
}
function renderRivals(){
  const enabled=RIVALS.filter(r=>r.enabled!==false);
  $('rivalCount').textContent=enabled.length;
  const completed=Math.max(0,...(DATA.manifest?.completed_gameweeks||[]));
  $('rivalsBody').innerHTML=RIVALS.map(r=>{
    const captain=player(r.last_deadline_captain);
    const chips=(r.chips_used||[]).map(c=>`${c.name} GW${c.event}`).join(', ')||'None reported';
    const stale=r.refresh_error||r.source_gameweek<completed;
    return `<tr><td><input type="checkbox" ${r.enabled!==false?'checked':''} aria-label="Include ${esc(r.team_name)}" onchange="changeRival(${Number(r.entry_id)},this.checked)"></td><td><strong>${esc(r.team_name||'Entry '+r.entry_id)}</strong><small>ID ${Number(r.entry_id)}</small>${r.refresh_error?'<span class="error-tiny">Refresh failed; showing saved snapshot</span>':''}</td><td><span class="tag ${stale?'warn':''}">GW${Number(r.source_gameweek)}</span><small>${esc(new Date(r.imported_at).toLocaleDateString())}</small></td><td class="number">${r.total_points_through_completed==null?'—':Number(r.total_points_through_completed)}</td><td>${esc(captain?.name||'Unknown')}</td><td>${esc(chips)}</td><td><button class="tiny secondary" onclick="changeRival(${Number(r.entry_id)},false,true)">Remove</button></td></tr>`;
  }).join('')||'<tr><td colspan="7" class="empty">Add rivals above. Your standard recommendations work without any rivals.</td></tr>';
  const counts={};enabled.forEach(r=>r.squad.forEach(id=>{counts[id]=(counts[id]||0)+1;}));
  const owned=Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,12);
  $('rivalOwnershipChart').innerHTML=svgChart({title:'Rival squad ownership',unit:'%',labels:owned.map(([id])=>(player(id)?.name||'#'+id)),series:[{name:'Rival squad ownership',color:'#644198',type:'bar',values:owned.map(([,n])=>100*n/enabled.length)}]});
  const histories=[...(ownMeta?.history?.length?[{...ownMeta,team_name:(ownMeta.team_name||'Your team')+' (last imported)'}]:[]),...enabled];
  const weeks=[...new Set(histories.flatMap(r=>(r.history||[]).map(h=>h.gameweek)))].sort((a,b)=>a-b);
  $('rivalHistoryChart').innerHTML=svgChart({title:'Finalized cumulative team points',unit:'pts',labels:weeks.map(w=>'GW'+w),series:histories.map((r,i)=>({name:r.team_name||'Entry '+r.entry_id,color:CHART_COLORS[i%CHART_COLORS.length],values:weeks.map(w=>r.history?.find(h=>h.gameweek===w)?.total_points??null)}))});
}
function populateAnalysisPickers(){
  const sorted=[...DATA.players].sort((a,b)=>a.name.localeCompare(b.name));
  const options=sorted.map(p=>`<option value="${p.id}">${esc(p.name)} · ${esc(p.club)} · ${p.position}</option>`).join('');
  ['analysisPlayer','comparePlayer1','comparePlayer2'].forEach((id,i)=>{
    const prior=$(id).value;$(id).innerHTML=(i?'<option value="">No comparison</option>':'')+options;
    if(player(prior))$(id).value=prior;else if(!i)$(id).value=selected.find(id=>player(id))||DATA.players[0]?.id||'';
  });
}
function inspectPlayer(id){
  $('analysisPlayer').value=String(id);$('comparePlayer1').value='';$('comparePlayer2').value='';
  showTab('analytics');renderPlayerDetail();$('playerProfile').scrollIntoView({behavior:'smooth',block:'start'});
}
function chartWeeks(ids){
  const latest=Math.max(0,...(DATA.manifest?.completed_gameweeks||[])),range=Number($('historyRange').value||10);
  const observed=ids.flatMap(id=>(ANALYTICS.players[id]?.history||[]).filter(h=>h.gameweek>latest-range).map(h=>h.gameweek));
  return [...new Set([...observed,...(DATA.manifest?.forecast_weeks||[])])].sort((a,b)=>a-b);
}
function pointSeries(id,weeks,color,name){
  const a=ANALYTICS.players[id];if(!a)return [];
  return [{name:name+' actual',color,values:weeks.map(w=>a.history.find(h=>h.gameweek===w)?.total_points??null)},
          {name:name+' forecast',color,dashed:true,values:weeks.map(w=>a.forecast.find(h=>h.gameweek===w)?.points??null)}];
}
function renderAnalytics(){
  if(!$('squadGraphs')||!DATA.ready)return;
  const owned=selected.map(player).filter(Boolean);
  if(!Object.keys(ANALYTICS.players).length){$('squadGraphs').innerHTML='<p class="history-empty">Loading player graph data…</p>';return;}
  $('squadGraphs').innerHTML=owned.map(p=>{
    const a=ANALYTICS.players[p.id],weeks=chartWeeks([p.id]);
    return `<article class="graph-card"><div class="card-head"><div><strong>${esc(p.name)}</strong><div class="small muted">${esc(p.club)} · ${p.position} · £${fmt(p.price)}</div></div><button class="tiny secondary" onclick="inspectPlayer(${p.id})">Inspect</button></div><div class="mini-stats"><span><b>${fmt(p.next_points)}</b> next GW pts</span><span><b>${fmt(p.minutes_recent,0)}</b> recent min/fixture</span></div>${svgChart({title:p.name+' actual and forecast points',height:200,labels:weeks.map(w=>'GW'+w),series:pointSeries(p.id,weeks,'#644198','Points'),unit:'pts'})}<div class="small muted">${a?.summary.games_recorded?fmt(a.summary.xgi_per90,2)+' xG + xA per 90':'No finalized current-season history'} · ${fmt(p.availability*100,0)}% availability flag</div></article>`;
  }).join('')||'<p class="history-empty">Import or add your squad in My team &amp; transfers to see a graph for every player. You can still inspect any player below.</p>';
  renderPlayerDetail();renderFixtureHeatmap();
}
function renderPlayerDetail(){
  const ids=[...new Set(['analysisPlayer','comparePlayer1','comparePlayer2'].map(id=>Number($(id).value)).filter(id=>player(id)))];
  if(!ids.length)return;
  const first=player(ids[0]),a=ANALYTICS.players[first.id],weeks=chartWeeks(ids);
  $('playerProfile').innerHTML=`<div class="profile-strip"><div><h3>${esc(first.full_name)}</h3><p class="small muted">${esc(first.club)} · ${first.position} · £${fmt(first.price)} · ${fmt(first.ownership)}% overall ownership</p>${first.news?'<p class="small">'+esc(first.news)+'</p>':''}</div><div class="profile-stats"><div><b>${fmt(first.next_points)}</b><span>Next GW forecast</span></div><div><b>${fmt(a?.summary.points,0)}</b><span>Finalized season points</span></div><div><b>${a?.summary.xgi_per90==null?'—':fmt(a.summary.xgi_per90,2)}</b><span>xG + xA per 90</span></div><div><b>${fmt(first.availability*100,0)}%</b><span>Official availability flag</span></div></div></div>`;
  $('pointsChart').innerHTML=svgChart({title:'Actual and forecast player points',unit:'pts',labels:weeks.map(w=>'GW'+w),series:ids.flatMap((id,i)=>pointSeries(id,weeks,CHART_COLORS[i],player(id).name))});
  const actualWeeks=weeks.filter(w=>DATA.manifest.completed_gameweeks.includes(w));
  [['minutesChart','minutes','minutes'],['xgChart','expected_goals','xG'],['xaChart','expected_assists','xA']].forEach(([target,field,unit])=>{
    $(target).innerHTML=svgChart({title:'Player '+unit+' by gameweek',unit,labels:actualWeeks.map(w=>'GW'+w),series:ids.map((id,i)=>({name:player(id).name,color:CHART_COLORS[i],type:field==='minutes'?'bar':undefined,values:actualWeeks.map(w=>ANALYTICS.players[id]?.history.find(h=>h.gameweek===w)?.[field]??null)}))});
  });
  $('playerComparisonTable').innerHTML=`<table><thead><tr><th>Player</th><th class="number">Finalized points</th><th class="number">Minutes</th><th class="number">xG</th><th class="number">xA</th><th class="number">xG + xA / 90</th><th class="number">Horizon forecast</th></tr></thead><tbody>${ids.map(id=>{const p=player(id),s=ANALYTICS.players[id]?.summary;return `<tr><td><strong>${esc(p.name)}</strong></td><td class="number">${s?fmt(s.points,0):'—'}</td><td class="number">${s?fmt(s.minutes,0):'—'}</td><td class="number">${s?fmt(s.xg,2):'—'}</td><td class="number">${s?fmt(s.xa,2):'—'}</td><td class="number">${s?.xgi_per90==null?'—':fmt(s.xgi_per90,2)}</td><td class="number">${fmt(p.horizon_points)}</td></tr>`;}).join('')}</tbody></table>`;
  $('analyticsNote').textContent=(ANALYTICS.note||'')+' Comparison table totals cover the whole finalized season; charts use your selected recent window. Hover or focus a chart point for its value.';
}
function renderFixtureHeatmap(){
  const weeks=DATA.manifest.forecast_weeks,owned=selected.map(player).filter(Boolean);
  $('fixtureHeatmap').innerHTML=owned.length?`<table><thead><tr><th>Player</th>${weeks.map(w=>`<th class="number">GW${w}</th>`).join('')}</tr></thead><tbody>${owned.map(p=>`<tr><td><button class="textbutton" onclick="inspectPlayer(${p.id})">${esc(p.name)}</button><small>${p.position} · ${esc(p.club)}</small></td>${weeks.map(w=>{const intensity=Math.min(.65,Math.max(.05,p['gw'+w]/15));return `<td class="heat-cell" style="background:rgba(155,212,133,${intensity})"><b>${fmt(p['gw'+w])} pts</b><small>${esc(p['fixtures'+w])}${p['games'+w]>1?' · Double GW':''}</small></td>`;}).join('')}</tr>`).join('')}</tbody></table>`:'<p class="history-empty">Add your squad to see upcoming fixtures.</p>';
}
function renderComparison(result,target){
  COMPARISONS[target]=result;
  const s=result.standard,a=result.rival_aware,c=result.comparison;
  const p=id=>esc(player(id)?.name||'#'+id);
  const loss=c.weighted_points_difference;
  const rows=[['Next GW, after hits',fmt(s.plans[0].expected_points_with_captain-s.hit_points),fmt(a.plans[0].expected_points_with_captain-a.hit_points)],['Weighted horizon, after hits',fmt(s.discounted_expected_points_after_hits),fmt(a.discounted_expected_points_after_hits)],['Captain',p(s.plans[0].captain),p(a.plans[0].captain)],['Transfer hit',s.hit_points+' pts',a.hit_points+' pts'],['Bank',s.remaining_bank==null?'—':'£'+fmt(s.remaining_bank),a.remaining_bank==null?'—':'£'+fmt(a.remaining_bank)]];
  const changed=c.different_players.length?c.different_players.map(p).join(', '):'The 15-player squad is unchanged.';
  $(target).innerHTML=`<div class="panel"><div class="paneltitle"><div><div class="eyebrow">Two views of the same forecasts</div><h2>Standard vs with rivals</h2><p class="small muted">${c.rivals_count} included rivals · ${c.style==='protect'?'Protect a lead':'Chase upside'} · ${fmt(c.influence*100,0)}% secondary influence</p></div><span class="tag">Maximum forecast sacrifice ${fmt(c.max_points_loss*100,0)}%</span></div><div class="tablewrap"><table class="plan-comparison"><thead><tr><th>Metric</th><th>Standard</th><th>With rivals</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join('')}</tbody></table></div><div class="notice"><b>Projected point difference:</b> ${loss>=0?'+':''}${fmt(loss,2)} weighted points. <b>Squad differences:</b> ${changed}${c.fallback_reason?'<br>'+esc(c.fallback_reason):''}</div><p class="table-note">${esc(c.note)}</p><div class="compare-choices" role="group" aria-label="Recommendation version"><button id="${target}Standard" aria-pressed="true" onclick="showComparisonVariant('${target}','standard')">Standard plan</button><button id="${target}Aware" aria-pressed="false" onclick="showComparisonVariant('${target}','rival_aware')">With rivals</button></div><h3>Next-GW scenarios against each rival</h3><div class="tablewrap scenario-table"><table><thead><tr><th>Rival</th><th class="number">Standard gap*</th><th class="number">With rivals gap*</th><th class="number">Standard ahead**</th><th class="number">With rivals ahead**</th><th class="number">Relative SD: standard / rivals</th></tr></thead><tbody>${result.scenarios.rivals.map(r=>`<tr><td><strong>${esc(r.team_name)}</strong><small>Squad snapshot GW${r.source_gameweek??'?'}</small></td><td class="number">${fmt(r.standard.expected_next_gap)}</td><td class="number">${fmt(r.rival_aware.expected_next_gap)}</td><td class="number">${fmt(r.standard.ahead_scenario_pct)}%</td><td class="number">${fmt(r.rival_aware.ahead_scenario_pct)}%</td><td class="number">${fmt(r.standard.relative_score_sd)} / ${fmt(r.rival_aware.relative_score_sd)}</td></tr>`).join('')}</tbody></table></div><p class="table-note" style="margin-top:12px">*Projected next-GW score difference, after your hits. **Fraction of ${result.scenarios.samples.toLocaleString()} illustrative scenarios where you outscore that rival this GW, not your chance of finishing ahead in the league. Relative SD measures variation in the score difference; lower means less relative risk.</p><p class="table-note">${esc(result.scenarios.note)}</p></div><div id="${target}Plan"></div>`;
  showComparisonVariant(target,'standard');$(target).scrollIntoView({behavior:'smooth',block:'start'});
}
function showComparisonVariant(target,version){
  const result=COMPARISONS[target];if(!result)return;
  $(target+'Standard').setAttribute('aria-pressed',String(version==='standard'));
  $(target+'Aware').setAttribute('aria-pressed',String(version==='rival_aware'));
  renderResult(result[version],target+'Plan',false);
}
