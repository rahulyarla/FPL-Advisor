/* Browser checks against dashboard_fixture.py. npm install --no-save playwright */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.FPL_PLAYWRIGHT_MODULE||'playwright');
async function main(){
  const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const base=process.env.FPL_TEST_URL||'http://127.0.0.1:8877';let count=0;
  try{
    for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
      const page=await browser.newPage({viewport});const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(base);await page.waitForFunction(()=>document.getElementById('analysisPlayer').options.length>0);
      await page.getByRole('button',{name:'My team & transfers',exact:true}).click();
      await page.locator('#entryId').fill('999');await page.locator('#importBtn').click();
      await page.waitForFunction(()=>document.querySelectorAll('#squadBody tr').length===15);
      await page.getByRole('button',{name:'Player graphs',exact:true}).click();
      assert.equal(await page.locator('.graph-card').count(),15);count++;
      assert.equal(await page.locator('.graph-card svg').count(),15);count++;
      const ids=await page.locator('#analysisPlayer option').evaluateAll(options=>options.slice(0,3).map(o=>o.value));
      await page.locator('#analysisPlayer').selectOption(ids[0]);await page.locator('#comparePlayer1').selectOption(ids[1]);await page.locator('#comparePlayer2').selectOption(ids[2]);
      assert.equal(await page.locator('#playerComparisonTable tbody tr').count(),3);count++;
      assert.equal(await page.locator('#fixtureHeatmap tbody tr').count(),15);count++;
      for(const name of ['pointsChart','minutesChart','xgChart','xaChart'])assert.equal(await page.locator('#'+name+' svg').count(),1);count++;
      assert.ok(await page.locator('#pointsChart polyline[stroke-dasharray]').count()>=6);count++;
      await page.getByRole('button',{name:'Model checks',exact:true}).click();
      assert.equal(await page.locator('#checksBody tr').count(),4);assert.match(await page.locator('#dataCoverage').textContent(),/development predictions/);count++;
      assert.equal(await page.locator('#feedbackChart svg').count(),1);assert.equal(await page.locator('#feedbackTableBody tr').count(),2);count++;
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);count++;
      fs.mkdirSync('test-artifacts',{recursive:true});
      await page.screenshot({path:`test-artifacts/model-checks-${viewport.width}.png`,fullPage:true});
      await page.getByRole('button',{name:/^Rivals /}).click();
      await page.locator('#rivalIds').fill('1001\n1002\nhttps://fantasy.premierleague.com/entry/1003/');await page.locator('#addRivalsBtn').click();
      await page.waitForFunction(()=>!document.getElementById('addRivalsBtn').disabled);
      assert.equal(await page.locator('#rivalsBody tr').count(),3);count++;
      await page.getByRole('button',{name:'My team & transfers',exact:true}).click();
      await page.locator('#tab-team .rivalToggle').check();await page.getByRole('button',{name:'Best XI & captain',exact:true}).click();
      await page.locator('#teamResultAware').waitFor();await page.locator('#teamResultAware').click();
      assert.equal(await page.locator('#teamResultAware').getAttribute('aria-pressed'),'true');count++;
      assert.equal(await page.locator('#teamResultPlan .playercard').count(),11);count++;
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert.equal(overflow,false);count++;
      const tooSmall=await page.locator('svg').evaluateAll(nodes=>nodes.filter(n=>{const r=n.getBoundingClientRect();return r.width>0&&r.width<100;}).length);assert.equal(tooSmall,0);count++;
      await page.locator('#compareQualitative').check();await page.getByRole('button',{name:'Best XI & captain',exact:true}).click();
      await page.locator('#teamResultWithEvidence').waitFor();await page.locator('#teamResultWithEvidence').click();
      assert.equal(await page.locator('#teamResultWithEvidence').getAttribute('aria-pressed'),'true');count++;
      assert.equal(await page.locator('#teamResultEvidencePlanPlan .playercard').count(),11);count++;
      await page.getByRole('button',{name:'News & expert views',exact:true}).click();
      assert.equal(await page.locator('#qualCoverageBody tr').count(),2);count++;
      await page.waitForFunction(()=>document.getElementById('qualSourceList').value.includes('FPLTom'));count++;
      assert.ok((await page.locator('#qualImportSource').boundingBox()).width>=200);count++;
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);count++;
      await page.screenshot({path:`test-artifacts/qualitative-${viewport.width}.png`,fullPage:true});
      assert.deepEqual(errors,[]);count++;
      console.log(`PASS browser viewport ${viewport.width}x${viewport.height}`);await page.close();
    }
    console.log(`${count} desktop/mobile browser checks passed.`);
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
