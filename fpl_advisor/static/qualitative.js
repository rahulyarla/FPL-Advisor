/* Two forecast views, evidence provenance and independent rival comparisons. */
let FORECAST_VIEW='stats';
const QUAL_COMPARISONS={};
function forecastRows(view){
  return view==='stats'?(DATA.stats_players||DATA.players):view==='learned'?(DATA.qualitative_learned_players||DATA.stats_players||DATA.players):(DATA.qualitative_candidate_players||DATA.stats_players||DATA.players);
}
function setForecastView(view,invalidate=true){
  if(view!=='stats'&&!DATA.qualitative_candidate_players?.length)return fail('Refresh evidence first to build the alternative.');
  FORECAST_VIEW=view;DATA.players=forecastRows(view);
  $('forecastStatsBtn').setAttribute('aria-pressed',String(view==='stats'));
  $('forecastQualBtn').setAttribute('aria-pressed',String(view!=='stats'));
  if(invalidate)invalidateResults();
  renderPlayers();renderSquad();renderQualitative();
}
function chooseQualView(){setForecastView($('validatedOnly').checked?'learned':'qualitative');}
function safeSourceLink(url,at=null){
  try{const u=new URL(url);if(u.protocol!=='https:')return '';if(at!=null&&['www.youtube.com','youtube.com','youtu.be'].includes(u.hostname))u.searchParams.set('t',Math.floor(at)+'s');return u.href;}catch(e){return '';}
}
function evidenceCard(s){
  const link=safeSourceLink(s.url,s.timestamp),date=new Date(s.published_at).toLocaleString();
  return `<article class="evidence-card"><div class="actions"><span class="tag">${esc(s.kind.replaceAll('_',' '))}</span><span class="small muted">${fmt(s.confidence*100,0)}% evidence score</span></div><p>${esc(s.excerpt)}</p><p class="small">${link?`<a href="${esc(link)}" target="_blank" rel="noreferrer">${esc(s.source)}${s.timestamp==null?'':' · '+Math.floor(s.timestamp/60)+':'+String(Math.floor(s.timestamp%60)).padStart(2,'0')}</a>`:esc(s.source)} · ${esc(date)}</p><small>${esc(s.coverage?.replaceAll('_',' ')||'')} ${s.stale?' · Stale: context only':!s.usable?' · Context only':''}${s.reported_claim?' · Reports another source':''}</small></article>`;
}
function renderQualitative(){
  const q=DATA.qualitative||{},rows=Object.entries(q.players||{}),learning=q.learning||{};
  $('qualStatus').textContent=q.as_of?`Evidence checked ${new Date(q.as_of).toLocaleString()} · ${q.signals?.length||0} extracted claims · ${q.affected_players||0} players with an experimental adjustment. ${FORECAST_VIEW==='stats'?'Showing stats only.':FORECAST_VIEW==='learned'?'Showing validated influence: '+fmt(q.learned_gain*100,0)+'%.':'Showing experimental stats + qualitative forecasts.'}`:'Refresh evidence to analyse recent videos and discussions.';
  $('qualLearning').textContent=learning.status||'Waiting for genuine paired pre-deadline forecasts and finalized outcomes.';
  $('qualPolicy').textContent=(q.policy||'')+' '+(q.experimental_note||'');
  $('qualCoverageBody').innerHTML=(q.coverage||[]).map(s=>`<tr><td><b>${esc(s.source)}</b></td><td>${esc(s.status?.replaceAll('_',' '))}</td><td class="number">${s.items||0}</td><td>${s.transcripts==null?'—':s.transcripts+' transcripts'}${s.comments==null?'':' · '+s.comments+' comments'}</td><td>${(s.details||[]).slice(0,3).map(d=>esc(d.reason)).join('; ')||'—'}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">No source reads recorded yet.</td></tr>';
  const term=$('evidenceSearch').value.toLowerCase();
  const changes=rows.filter(([id,p])=>p.signal_count&&((player(id)?.name||'').toLowerCase().includes(term))).sort((a,b)=>Math.abs(b[1].experimental_delta)-Math.abs(a[1].experimental_delta));
  $('qualPlayerRows').innerHTML=changes.map(([id,p])=>`<tr><td><button class="textbutton" onclick="inspectPlayer(${Number(id)})">${esc(player(id)?.name||id)}</button></td><td>${p.usable_count} / ${p.signal_count}</td><td class="number">${fmt(p.stats_points)}</td><td class="number">${fmt(p.experimental_points)}</td><td class="number">${p.experimental_delta>=0?'+':''}${fmt(p.experimental_delta,3)}</td><td>${esc([...p.notes,...(p.roles||[]).map(k=>k.replaceAll('_',' ')+' (review)'),...(p.price_context||[]).map(k=>k.replaceAll('_',' ')+' (context)')].join(' · ')||'Inspect sources below.')}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No matched player claims. Missing coverage is shown above.</td></tr>';
  $('qualReview').textContent=`${q.duplicates_removed||0} repeated passages removed; ${(q.review||[]).length} ambiguous passages retained for review with zero influence.`;
  $('qualReviewDetails').innerHTML=(q.review||[]).slice(0,12).map(r=>`<li>${esc(r.source)}: ${esc(r.reason)} · ${(r.player_ids||[]).map(id=>esc(player(id)?.name||id)).join(', ')}</li>`).join('');
  const gws=learning.gameweeks||[];
  $('qualResultsBody').innerHTML=gws.length?gws.map(r=>`<tr><td>GW${r.gameweek}</td><td>${r.affected}</td><td>${r.affected_stats?fmt(r.affected_stats.rmse,3):'—'}</td><td>${r.affected_experimental?fmt(r.affected_experimental.rmse,3):'—'}</td><td>${r.minutes_stats_mae==null?'—':fmt(r.minutes_stats_mae,1)}</td><td>${r.minutes_proxy_mae==null?'—':fmt(r.minutes_proxy_mae,1)}</td></tr>`).join(''):'<tr><td colspan="6" class="empty">Waiting for finalized paired forecasts. Existing historical model errors do not validate sentiment.</td></tr>';
  $('qualResultsDownload').classList.toggle('hidden',!gws.length);
}
function renderPlayerEvidence(id){
  const q=DATA.qualitative||{},p=q.players?.[id];
  if(!$('playerEvidence'))return;
  if(!p||!p.signal_count){$('playerEvidence').innerHTML='<p class="small muted">No matched recent qualitative evidence for this player.</p>';$('playerEvidenceTrend').innerHTML='';return;}
  $('playerEvidence').innerHTML=`<h3>Recent qualitative evidence</h3><p class="small muted">Stats ${fmt(p.stats_points)} pts · experimental ${fmt(p.experimental_points)} pts · validated change ${fmt(p.learned_delta,3)} pts. ${esc(p.notes.join(' '))}</p><div class="evidence-grid">${p.signals.slice(0,12).map(evidenceCard).join('')}</div>`;
  const days=[...new Set(p.signals.map(s=>s.published_at.slice(0,10)))].sort();
  $('playerEvidenceTrend').innerHTML=svgChart({title:'Captured source views over time',unit:'evidence',labels:days,series:[{name:'Positive mentions',color:'#16866e',values:days.map(d=>p.signals.filter(s=>s.published_at.startsWith(d)&&s.direction>0&&s.usable).length)},{name:'Cautionary mentions',color:'#bb536c',values:days.map(d=>p.signals.filter(s=>s.published_at.startsWith(d)&&s.direction<0&&s.usable).length)}]});
}
async function refreshEvidence(){
  try{$('errorBox').classList.add('hidden');await request('/api/qualitative/refresh',{});wasRunning=true;poll();}catch(e){fail(e.message);}
}
async function loadQualSources(){
  try{const cfg=await request('/api/qualitative/sources');window.QUAL_SETTINGS=cfg;$('qualSourceList').value=cfg.sources.map(s=>`${s.kind}|${s.handle||s.subreddit}|${s.name}|${s.enabled===false?'off':'on'}`).join('\n');}catch(e){fail(e.message);}
}
async function saveQualSources(){
  try{
    const old=window.QUAL_SETTINGS||{};
    const sources=$('qualSourceList').value.split('\n').filter(s=>s.trim()).map(line=>{const [kind,key,name,enabled]=line.split('|').map(s=>s.trim());const match=(old.sources||[]).find(s=>s.kind===kind&&(s.handle||s.subreddit)===key);return {kind,name:name||key,[kind==='youtube'?'handle':'subreddit']:key,enabled:enabled!=='off',...(match?.channel_id?{channel_id:match.channel_id}:{})};});
    window.QUAL_SETTINGS=await request('/api/qualitative/sources',{...old,sources});await refreshEvidence();
  }catch(e){fail(e.message);}
}
async function importEvidence(){
  const button=$('qualImportBtn');button.disabled=true;
  try{const date=$('qualImportDate').value;if(!date)throw new Error('Enter the original publication date and time.');const result=await request('/api/qualitative/import',{url:$('qualImportUrl').value,source:$('qualImportSource').value,text:$('qualImportText').value,published_at:new Date(date).toISOString(),origin_url:$('qualOriginUrl').value});$('qualImportStatus').textContent=`Saved ${result.coverage.replaceAll('_',' ')}. Analysing…`;$('qualImportText').value='';wasRunning=true;poll();}catch(e){fail(e.message);}finally{button.disabled=false;}
}
function renderQualComparison(result,target){
  QUAL_COMPARISONS[target]=result;
  const base=r=>r.standard||r;
  const s=base(result.stats_only),a=base(result.with_qualitative),name=id=>esc((DATA.stats_players||DATA.players).find(p=>p.id===id)?.name||id);
  const ids=[...new Set([...s.squad,...a.squad])];
  const changes=result.player_changes.filter(p=>ids.includes(p.id));
  $(target).innerHTML=`<div class="panel"><h2>Stats only vs stats + qualitative</h2><p class="notice">${esc(result.alternate_view==='learned'?result.learning.status:result.note)}</p><div class="tablewrap"><table><thead><tr><th>Metric</th><th>Stats only</th><th>Stats + qualitative</th></tr></thead><tbody><tr><td>Next GW, captain included, before hits</td><td>${fmt(s.plans[0].expected_points_with_captain)}</td><td>${fmt(a.plans[0].expected_points_with_captain)}</td></tr><tr><td>Captain</td><td>${name(s.plans[0].captain)}</td><td>${name(a.plans[0].captain)}</td></tr><tr><td>Hit points</td><td>${s.hit_points}</td><td>${a.hit_points}</td></tr><tr><td>Buy</td><td>${s.bought.map(name).join(', ')||'—'}</td><td>${a.bought.map(name).join(', ')||'—'}</td></tr></tbody></table></div><p class="small muted">Each column uses its own forecast assumptions. A higher number alone does not establish a better team.</p><div class="compare-choices"><button id="${target}StatsOnly" onclick="showQualPlan('${target}','stats_only')" aria-pressed="true">Stats only</button><button id="${target}WithEvidence" onclick="showQualPlan('${target}','with_qualitative')" aria-pressed="false">Stats + qualitative</button></div><h3>Evidence behind changed assumptions</h3><ul class="plainlist">${changes.map(p=>`<li><button class="textbutton" onclick="inspectPlayer(${p.id})">${name(p.id)}</button>: ${fmt(p.stats_points)} → ${fmt(result.alternate_view==='learned'?p.stats_points+p.learned_delta:p.experimental_points)} pts. ${esc(p.notes.join(' '))} Open the player for source passages.</li>`).join('')||'<li>No adjustment affects either selected squad. Identical plans are a valid result.</li>'}</ul></div><div id="${target}EvidencePlan"></div>`;
  showQualPlan(target,'stats_only');
}
function showQualPlan(target,version){
  const result=QUAL_COMPARISONS[target];if(!result)return;
  FORECAST_VIEW=version==='stats_only'?'stats':result.alternate_view;
  DATA.players=forecastRows(FORECAST_VIEW);
  $('forecastStatsBtn').setAttribute('aria-pressed',String(FORECAST_VIEW==='stats'));
  $('forecastQualBtn').setAttribute('aria-pressed',String(FORECAST_VIEW!=='stats'));
  $(target+'StatsOnly').setAttribute('aria-pressed',String(version==='stats_only'));
  $(target+'WithEvidence').setAttribute('aria-pressed',String(version==='with_qualitative'));
  const plan=result[version];if(plan.standard)renderComparison(plan,target+'EvidencePlan');else renderResult(plan,target+'EvidencePlan',false);
  renderPlayers();renderSquad();renderQualitative();
}
