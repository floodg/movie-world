const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('../tizen/node_modules/@playwright/test');
(async () => {
  const child = spawn('python', ['scripts/personal_browser_fixture.py'], {cwd:process.cwd(),stdio:['ignore','pipe','inherit']});
  let browser;
  try {
    const port = await new Promise((resolve,reject) => {
      child.once('exit',code=>reject(new Error('fixture exited '+code)));
      child.stdout.once('data',data=>resolve(JSON.parse(data.toString().trim()).port));
    });
    const base = 'http://127.0.0.1:'+port;
    browser = await chromium.launch({headless:true,channel:'chrome'});
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    let fail=false;
    const rows = [
      {event_id:'one',seq:3,timestamp:Date.now(),source:'server',level:'info',operation:'preparation',message:'The Matrix · downloading',movie_id:'123'},
      {event_id:'two',seq:2,timestamp:Date.now()-60000,source:'android',level:'error',operation:'subtitles',message:'<img src=x onerror=alert(1)> SubDL request failed',movie_id:'123'}
    ];
    await page.route('**/api/operations?*',route=>{
      if (fail) return route.fulfill({status:503,body:'Unavailable'});
      const url=new URL(route.request().url());
      let events=rows.filter(r=>(!url.searchParams.get('source')||r.source===url.searchParams.get('source'))&&(!url.searchParams.get('level')||r.level===url.searchParams.get('level'))&&(!url.searchParams.get('q')||r.message.includes(url.searchParams.get('q'))));
      if(url.searchParams.get('before')) events=[{...rows[0],event_id:'older',seq:1,message:'Captions generated'}];
      return route.fulfill({json:{events,next:url.searchParams.get('before')?null:2}});
    });
    await page.goto(base+'/operations.html');
    await page.waitForFunction(()=>document.querySelectorAll('article').length===2);
    assert.equal(await page.locator('article img').count(),0);
    await page.screenshot({path:'.local/operations-desktop.png',fullPage:true});
    await page.locator('#source').selectOption('android');
    await page.waitForFunction(()=>document.querySelectorAll('article').length===1);
    assert.match(await page.locator('article').innerText(),/Android app/);
    await page.locator('#source').selectOption('');
    await page.waitForFunction(()=>document.querySelectorAll('article').length===2);
    await page.locator('#older').click();
    await page.waitForFunction(()=>document.querySelectorAll('article').length===3);
    fail=true; await page.locator('#refresh').click();
    await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('503'));
    fail=false; await page.locator('#refresh').click();
    await page.waitForFunction(()=>document.querySelectorAll('article').length===2);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'.local/operations-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log('Operation history browser checks passed (filters, pagination, errors, safe text, mobile layout).');
  } finally { if(browser) await browser.close(); child.kill(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
