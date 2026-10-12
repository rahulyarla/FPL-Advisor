/* Browser checks against dashboard_fixture.py. npm install --no-save playwright */
const assert=require('node:assert/strict');
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
      assert.deepEqual(errors,[]);count++;
      console.log(`PASS browser viewport ${viewport.width}x${viewport.height}`);await page.close();
    }
    console.log(`${count} desktop/mobile browser checks passed.`);
  }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
