/* HTTP + DOM smoke checks. npm install --no-save jsdom before running. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM,VirtualConsole}=require(process.env.FPL_JSDOM_MODULE||'jsdom');
const base=process.env.FPL_TEST_URL||'http://127.0.0.1:8877';
const wait=async(fn,description)=>{const until=Date.now()+30000;while(Date.now()<until){if(fn())return;await new Promise(r=>setTimeout(r,30));}throw new Error('Timed out: '+description);};
async function main(){
  const errors=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>{if(!/CSS stylesheet/.test(e.message))errors.push(e.message);});
  const dom=await JSDOM.fromURL(base,{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.fetch=(url,options)=>fetch(new URL(url,base),options);w.HTMLElement.prototype.scrollIntoView=function(){};}});
  const w=dom.window,d=w.document,$=id=>d.getElementById(id);let passed=0;
  const check=(name,fn)=>{fn();passed++;console.log('PASS '+name);};
  try{
    await wait(()=>$('analysisPlayer').options.length>0,'dashboard assets and analytics');
    check('six tabs and loaded forecasts',()=>{assert.equal(d.querySelectorAll('nav button').length,6);assert.equal($('playersBody').rows.length,40);});
    check('rival comparison is off initially',()=>assert.equal(d.querySelector('.rivalToggle').checked,false));
    $('entryId').value='999';await w.importTeam();
    check('team import populates 15 players',()=>assert.equal($('squadBody').rows.length,15));
    w.showTab('analytics');
    check('a graph for every squad player',()=>{assert.equal(d.querySelectorAll('.graph-card').length,15);assert.equal(d.querySelectorAll('.graph-card svg').length,15);});
    const ids=[...$('analysisPlayer').options].slice(0,3).map(o=>o.value);
    $('analysisPlayer').value=ids[0];$('comparePlayer1').value=ids[1];$('comparePlayer2').value=ids[2];w.renderPlayerDetail();
    check('three player comparison and forecast distinction',()=>{assert.equal($('playerComparisonTable').querySelectorAll('tbody tr').length,3);assert.equal($('pointsChart').querySelectorAll('polyline[stroke-dasharray]').length,3);});
    check('minutes xG xA and accessible values',()=>{for(const id of ['minutesChart','xgChart','xaChart'])assert.ok($(id).querySelector('svg'));assert.ok($('pointsChart').querySelector('circle[aria-label]'));});
    check('fixture heatmap covers all squad players',()=>assert.equal($('fixtureHeatmap').querySelectorAll('tbody tr').length,15));
    $('rivalIds').value='999';await w.addRivals();
    check('own entry cannot be added as rival',()=>assert.match($('errorBox').textContent,/own entry/));
    $('rivalIds').value='1001, 1002\nhttps://fantasy.premierleague.com/entry/1003/\n1001';await w.addRivals();
    check('bulk imports IDs links and deduplicates',()=>{assert.equal($('rivalsBody').rows.length,3);assert.equal($('rivalCount').textContent,'3');});
    await w.changeRival(1002,false);
    check('individual inclusion changes active count',()=>assert.equal($('rivalCount').textContent,'2'));
    await w.refreshRivalTeams();
    check('refresh retains excluded rivals and charts',()=>{assert.equal($('rivalCount').textContent,'2');assert.ok($('rivalOwnershipChart').querySelector('svg'));assert.ok($('rivalHistoryChart').querySelector('svg'));});
    w.showTab('team');await w.choose('lineup');
    check('standard lineup works with rivals ignored',()=>{assert.ok($('teamResult').querySelector('.pitch'));assert.equal($('teamResult').querySelectorAll('.playercard').length,11);assert.equal($('teamResultStandard'),null);});
    w.setRivalComparison(true);await w.choose('lineup');
    check('comparison displays both plans and scenarios',()=>{assert.ok($('teamResultStandard'));assert.ok($('teamResultAware'));assert.equal($('teamResult').querySelectorAll('.scenario-table tbody tr').length,2);});
    w.showComparisonVariant('teamResult','rival_aware');
    check('version button switches the displayed team',()=>assert.equal($('teamResultAware').getAttribute('aria-pressed'),'true'));
    w.inspectPlayer(Number(ids[1]));
    check('recommendation player opens analytics',()=>{assert.ok($('tab-analytics').classList.contains('active'));assert.equal($('analysisPlayer').value,ids[1]);});
    $('rivalInfluence').value='15';$('rivalStyle').value='chase';$('maxPointsLoss').value='.02';
    // Browser selects use their actual HTML option values.
    $('maxPointsLoss').value='0.02';w.saveRivalSettings();w.showTab('wildcard');await w.choose('wildcard');
    check('wildcard supports the two versions',()=>{assert.ok($('wcResultStandard'));assert.ok($('wcResultAware'));});
    w.showTab('team');$('economicsConfirmed').checked=false;await w.choose('transfers');
    check('transfer economics remain guarded',()=>assert.match($('errorBox').textContent,/Confirm your current bank/));
    $('economicsConfirmed').checked=true;await w.choose('transfers');
    check('transfers support rival comparison after confirmation',()=>assert.ok($('teamResultAware')));
    await w.changeRival(1003,false,true);
    check('removal updates and persists saved rivals',()=>assert.equal($('rivalsBody').rows.length,2));
    check('no uncaught JavaScript errors',()=>assert.deepEqual(errors,[]));
    if(process.env.FPL_DOM_OUTPUT)fs.writeFileSync(process.env.FPL_DOM_OUTPUT,dom.serialize());
    console.log(`${passed} HTTP/DOM smoke checks passed.`);
  }finally{dom.window.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
