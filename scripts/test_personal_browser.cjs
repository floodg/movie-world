const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('../tizen/node_modules/@playwright/test');
(async () => {
  const child = spawn('python', ['scripts/personal_browser_fixture.py'], {cwd: process.cwd(), env: process.env, stdio:['ignore','pipe','inherit']});
  let browser;
  try {
    const port = await new Promise((resolve,reject) => {
      child.once('exit', code => reject(new Error('fixture exited '+code)));
      child.stdout.once('data', data => resolve(JSON.parse(data.toString().trim()).port));
    });
    const base = 'http://127.0.0.1:'+port;
    browser = await chromium.launch({headless:true,channel:"chrome"});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    const stamp=Date.now();
    await page.addInitScript(({stamp}) => {
      localStorage.setItem('movie-world-favourites', JSON.stringify([{id:1305,title:'Saved title',updated_at:new Date(stamp).toISOString()}]));
      localStorage.setItem('movie-world-watch-progress', JSON.stringify([{movie_id:1305,position:300,duration:7200,finished:false,updated_at:stamp/1000}]));
    },{stamp});
    await page.goto(base);
    await page.waitForFunction(() => localStorage.getItem('movie-world-pc-migrated-local')==='yes');
    let state=await (await page.request.get(base+'/api/personal')).json();
    assert.equal(state.snapshot.favourites.length,1);
    assert.equal(state.snapshot.progress[0].position,300);
    assert.equal(await page.locator('#account-email').count(),0);
    assert.equal(await page.locator('script[src*="supabase"]').count(),0);
    await page.locator('#continue-watching .watch-action').click();
    await page.waitForFunction(() => window.MovieWorldSync.exportWatchForCloud().some(r => r.movie_id===1305 && r.finished));
    await page.evaluate(() => window.MovieWorldCloud.syncWatchProgress());
    state=await (await page.request.get(base+'/api/personal')).json();
    assert.equal(state.snapshot.progress.find(r=>r.movieId==='1305').finished,true);
    assert.equal(await page.locator('#continue-watching').isVisible(),false);
    await page.reload();
    await page.waitForFunction(() => window.MovieWorldCloud.session());
    assert.equal(await page.locator('#continue-watching').isVisible(),false);
    await page.request.post(base+'/api/personal/merge',{data:{scope:'local',
      favourites:[{movieId:'1305',updated:stamp+1000,removed:stamp+1000}],
      progress:[{movieId:'1305',updated:stamp+1000,position:0,duration:7200,finished:false}]}});
    await page.evaluate(() => window.MovieWorldCloud.syncFavourites());
    assert.ok(await page.evaluate(() => window.MovieWorldSync.exportFavouritesForCloud().find(r=>r.movie_id===1305).removed_at));
    assert.equal(await page.evaluate(() => window.MovieWorldSync.exportWatchForCloud()[0].position_seconds),0);
    await page.context().setOffline(true);
    await page.evaluate(() => window.MovieWorldCloud.pushFavourite(1306,null,new Date().toISOString()));
    await page.waitForTimeout(600);
    assert.ok(await page.evaluate(() => JSON.parse(localStorage.getItem('movie-world-pc-sync-queue')).length>0));
    await page.context().setOffline(false);
    await page.evaluate(() => window.MovieWorldCloud.syncFavourites());
    state=await (await page.request.get(base+'/api/personal')).json();
    assert.ok(state.snapshot.favourites.some(r=>r.movieId==='1306'));
    assert.equal(errors.length,0,errors.join('\n'));
    console.log('PASS browser cache adoption, server refresh, removal, resume reset, offline queue, and reconnect');
  } finally {
    if(browser)await browser.close();
    child.kill();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
