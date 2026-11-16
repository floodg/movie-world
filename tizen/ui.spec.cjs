const {test,expect} = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname,'../.local/tizen-app');
const movie = {id:1,title:'The Shawshank Redemption',year:1994,rating:9.3,poster_url:'https://image.tmdb.org/t/p/w500/poster.jpg',backdrop_url:'https://image.tmdb.org/t/p/original/backdrop.jpg',genres:'Drama',overview:'Two imprisoned men bond over a number of years, finding solace and eventual redemption through acts of common decency.',cast:[{id:1,name:'Morgan Freeman'}]};
const other = {...movie,id:2,title:'The Godfather',year:1972,rating:9.2};
test.afterEach(async ({page}) => {
  expect((await page.evaluate(() => window.__uiErrors || [])).join('\n')).toBe('');
});

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {
    window.__uiErrors = [];
    window.addEventListener('error', event => { if (event.message) window.__uiErrors.push(event.message); });
    window.addEventListener('unhandledrejection', event => window.__uiErrors.push(String(event.reason)));
    localStorage.setItem('mw-pc','http://192.168.0.20');localStorage.setItem('mw-token','a'.repeat(32));
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'image.tmdb.org') {
      const backdrop = url.pathname.includes('backdrop');
      return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${backdrop?1920:500}" height="${backdrop?1080:750}"><defs><linearGradient id="g"><stop stop-color="#292c38"/><stop offset="1" stop-color="#70624a"/></linearGradient></defs><path fill="url(#g)" d="M0 0h1920v1080H0z"/><path fill="#0a0a0f" opacity=".4" d="M240 100h80v900h-80zm200 0h80v900h-80z"/></svg>`});
    }
    if (url.pathname.startsWith('/api/')) {
      let body;
      if (url.pathname === '/api/tizen-catalogue/home') body = {featured:movie,top_rated:[movie,other],genres:[{id:1,name:'Drama'},{id:2,name:'Crime'}],rows:[{genre:{id:1,name:'Drama'},movies:[movie,other]}]};
      else if (url.pathname === '/api/resume') body = {points:[{movie_id:2,position:120,duration:1000}],movies:[other]};
      else if (url.pathname === '/api/actors') body = {actors:url.searchParams.get('q') === 'nothing' ? [] : [{id:1,name:'Morgan Freeman',movie_count:12},{id:2,name:'Morgan Fairchild',movie_count:1}]};
      else if (url.pathname === '/api/tizen-catalogue/search') body = {movies:url.searchParams.get('q') === 'nothing' ? [] : [movie,other]};
      else if (url.pathname.endsWith('/similar')) body = [other];
      else if (/\/api\/movies\/\d+$/.test(url.pathname)) body = url.pathname.endsWith('/2') ? other : movie;
      else if (url.pathname === '/api/tizen/prepare') body = {job:{id:'b'.repeat(32),movie_id:1,title:movie.title,active:true,state:'downloading'}};
      else if (url.pathname.endsWith('/status')) body = {job:{id:'b'.repeat(32),movie_id:1,title:movie.title,active:true,state:'downloading',detail:'Downloading from Telegram',progress:{done:50,total:100}}};
      else body = {job:{active:false}};
      return route.fulfill({json:body,headers:{'Access-Control-Allow-Origin':'*'}});
    }
    const relative = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname.slice(1));
    if (relative.includes('$WEBAPIS')) return route.fulfill({body:''});
    const file = path.resolve(root,relative);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return route.fulfill({status:404,body:'Not found'});
    const types = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2','.png':'image/png'};
    return route.fulfill({body:fs.readFileSync(file),contentType:types[path.extname(file)] || 'application/octet-stream'});
  });
  await page.goto('http://tizen.test/');
  await expect(page.locator('.hero h1')).toHaveText(movie.title);
  await page.evaluate(() => document.fonts.ready);
});

async function startSubtitleMovie(page, tracks) {
  await page.addInitScript(tracks => {
    let state = 'NONE';
    window.__subtitleCalls = [];
    window.webapis = {avplay:{
      open(){state='IDLE';}, setDisplayRect(){}, setListener(){},
      prepareAsync(done){state='READY';done();}, play(){state='PLAYING';},
      pause(){state='PAUSED';}, close(){state='NONE';}, getState(){return state;},
      getDuration(){return 100000;}, getCurrentTime(){return 1000;},
      getTotalTrackInfo(){return tracks;},
      setSelectTrack(type,index){window.__subtitleCalls.push(['track',type,index]);},
      setSilentSubtitle(value){window.__subtitleCalls.push(['silent',value]);},
      setSubtitlePosition(value){window.__subtitleCalls.push(['delay',value]);}
    }};
  }, tracks);
  await page.route('**/api/tizen/*/status', route => route.fulfill({json:{job:{
    id:'b'.repeat(32), movie_id:1, title:movie.title, active:true, media_url:'http://pc/movie.mkv'
  }},headers:{'Access-Control-Allow-Origin':'*'}}));
  await page.route('**/api/tizen/*/progress', route => route.fulfill({json:{job:{active:true}},headers:{'Access-Control-Allow-Origin':'*'}}));
  await page.reload();
  await expect(page.locator('.hero h1')).toHaveText(movie.title);
  await page.evaluate(() => window.MovieWorldTv.play({id:1,title:'Test'}));
  await expect(page.locator('body')).toHaveClass(/mw-playing/);
}

test('subtitle menu selects actual track IDs, adjusts timing and Back keeps playing', async ({page}) => {
  await startSubtitleMovie(page,[{type:'AUDIO',index:0},{type:'TEXT',index:4,extra_info:'{"track_lang":"eng"}'}]);
  await page.keyboard.press('ArrowDown');
  const panel = page.locator('.subtitle-panel').last();
  await expect(panel).toBeVisible();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => window.__subtitleCalls)).toContainEqual(['track','TEXT',4]);
  expect(await page.evaluate(() => window.__subtitleCalls)).toContainEqual(['silent',false]);
  expect(await page.evaluate(() => window.__subtitleCalls)).toContainEqual(['delay',500]);
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(page.locator('body')).toHaveClass(/mw-playing/);
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => window.__subtitleCalls)).toContainEqual(['silent',true]);
});

test('subtitle menu handles movies with no subtitles', async ({page}) => {
  await startSubtitleMovie(page,[]);
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.subtitle-panel').last()).toContainText('No embedded subtitles available');
  await page.keyboard.press('Escape');
  await expect(page.locator('body')).toHaveClass(/mw-playing/);
});

test('Kotlin home theme, fonts, tabs and launcher icon',async ({page}) => {
  await expect(page.locator('.wordmark')).toHaveText('MOVIEWORLD');
  await expect(page.locator('.tabs button')).toHaveText(['Home','Search','My List','History']);
  await expect(page.locator('body')).toHaveCSS('background-color','rgb(10, 10, 15)');
  await expect(page.locator('.hero .primary')).toHaveCSS('background-color','rgb(245, 165, 36)');
  await expect(page.locator('.hero h1')).toHaveCSS('font-family',/RobotoCondensed/);
  await page.screenshot({path:path.resolve(__dirname,'../.local/tizen-home.png')});
  expect(fs.readFileSync(path.join(root,'icon.png')).subarray(1,4).toString()).toBe('PNG');
  expect(fs.readFileSync(path.join(root,'icon.svg'),'utf8')).toContain('M48,44 L66,54 L48,64 Z');
});

test('My List persists and detail returns focus', async ({page}) => {
  await page.locator('.hero .secondary').click();
  await page.locator('[data-tab="list"]').click();
  const saved = page.locator('#screen .poster');
  await expect(saved).toHaveCount(1);
  await saved.click();
  await expect(page.locator('#detail h1')).toHaveText(movie.title);
  await expect(page.locator('#detail')).toContainText('More like this');
  await page.screenshot({path:path.resolve(__dirname,'../.local/tizen-detail.png')});
  await page.keyboard.press('Escape');
  await expect(saved).toBeFocused();
  await page.reload();
  await page.locator('[data-tab="list"]').click();
  await expect(page.locator('#screen .poster')).toHaveCount(1);
});

test('search supports actor, genre, year filters and empty results', async ({page}) => {
  await page.locator('[data-tab="search"]').click();
  await page.locator('.segments').getByRole('button',{name:'Actor',exact:true}).click();
  await expect(page.locator('.segments .selected')).toHaveText('Actor');
  await page.getByRole('combobox').fill('Morgan');
  await expect(page.getByRole('option',{name:'Morgan Freeman 12 movies'})).toBeVisible();
  await expect(page.getByRole('option',{name:'Morgan Fairchild 1 movie',exact:true})).toBeVisible();
  await page.getByRole('option',{name:'Morgan Freeman 12 movies'}).click();
  await page.locator('.chips').getByRole('button',{name:'Crime',exact:true}).click();
  await page.getByRole('button',{name:'Filters',exact:true}).click();
  await page.getByRole('spinbutton',{name:'From year'}).fill('1990');
  await page.getByRole('spinbutton',{name:'To year'}).fill('2000');
  const request = page.waitForRequest(r => r.url().includes('/api/tizen-catalogue/search') && r.url().includes('years=1990-2000'));
  await page.getByRole('button',{name:'Apply',exact:true}).click();
  const url = new URL((await request).url());
  expect(url.searchParams.get('actor_id')).toBe('1');
  expect(url.searchParams.get('genre')).toBe('2');
  await expect(page.locator('#search-results .poster')).toHaveCount(2);
  await page.screenshot({path:path.resolve(__dirname,'../.local/tizen-search.png')});
  await page.getByRole('combobox').fill('nothing');
  await expect(page.locator('#search-results')).toContainText('No actors match');
});

test('actor suggestions require two letters and support remote selection and dismissal', async ({page}) => {
  await page.locator('[data-tab="search"]').click();
  await page.getByRole('button',{name:'Actor',exact:true}).click();
  const input = page.getByRole('combobox');
  await input.fill('M');
  await expect(page.locator('#search-results')).toContainText('Type at least two letters');
  await expect(page.getByRole('listbox')).toBeHidden();
  await input.fill('Morgan');
  await expect(page.getByRole('listbox')).toBeVisible();
  await page.screenshot({path:path.resolve(__dirname,'../.local/tizen-actor-search.png')});
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('option').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('option').last()).toBeFocused();
  await page.keyboard.press('ArrowUp');
  const picked = page.waitForRequest(r => r.url().includes('actor_id=1'));
  await page.keyboard.press('Enter');
  await picked;
  await expect(input).toHaveValue('Morgan Freeman');
  await expect(page.getByRole('listbox')).toBeHidden();
  await input.fill('Morgan');
  await expect(page.getByRole('listbox')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('listbox')).toBeHidden();
  await expect(page.locator('[data-tab="search"]')).toHaveAttribute('aria-current','page');
});

test('a slower actor query cannot replace newer suggestions', async ({page}) => {
  await page.route('**/api/actors?q=Mo', async route => {
    await new Promise(resolve => setTimeout(resolve, 900));
    await route.fulfill({json:{actors:[{id:99,name:'Old Actor',movie_count:9}]},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.locator('[data-tab="search"]').click();
  await page.getByRole('button',{name:'Actor',exact:true}).click();
  const input = page.getByRole('combobox');
  const slow = page.waitForRequest(r => r.url().endsWith('/api/actors?q=Mo'));
  await input.fill('Mo'); await slow;
  const oldResponse = page.waitForResponse(r => r.url().endsWith('/api/actors?q=Mo'));
  await input.fill('Morgan');
  await expect(page.getByRole('option',{name:'Morgan Freeman 12 movies'})).toBeVisible();
  await oldResponse;
  await expect(page.getByRole('option',{name:'Old Actor 9 movies'})).toHaveCount(0);
  await expect(page.getByRole('option',{name:'Morgan Freeman 12 movies'})).toBeVisible();
});

test('remote navigation, settings focus trap, and preparation cancellation', async ({page}) => {
  await page.locator('.hero .primary').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.hero .secondary')).toBeFocused();
  await page.locator('#settings-open').click();
  await expect(page.locator('#connection')).toBeVisible();
  await page.getByRole('button',{name:'Save and connect'}).focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('#settings-close')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#connection')).toBeHidden();
  await page.locator('.hero .primary').click();
  await expect(page.locator('.player-status')).toContainText('Downloading from Telegram');
  const cancel = page.waitForRequest(r => r.url().endsWith('/cancel'));
  await page.keyboard.press('Escape');
  await cancel;
  await expect(page.locator('.player-status')).toBeHidden();
});

test('selector starts on the first movie; title is not selectable; settings is the top stop', async ({page}) => {
  await expect(page.locator('#screen .poster').first()).toBeFocused();
  expect(await page.locator('.wordmark').evaluate(node => node.tagName + node.tabIndex)).toBe('DIV-1');
  const box = await page.locator('#settings-open').boundingBox();
  expect(Math.abs(box.width - box.height)).toBeLessThan(1.5);
  await page.keyboard.press('ArrowUp');
  await expect(page.locator('.hero button:focus')).toHaveCount(1);
  for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowUp');
  await expect(page.locator('#settings-open')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.hero button:focus')).toHaveCount(1);
});

test('poster row scrolls under the selector until the end, then the selector moves', async ({page}) => {
  const many = Array.from({length: 24}, (_, i) => ({...movie, id: 100 + i, title: 'Movie ' + i}));
  await page.route('**/api/tizen-catalogue/home*', route => route.fulfill({json:{featured:movie,top_rated:many,genres:[],rows:[]},headers:{'Access-Control-Allow-Origin':'*'}}));
  await page.reload();
  const row = page.locator('.rail[aria-label="Top Rated"] .poster-row');
  const first = row.locator('.poster').first();
  await expect(page.locator('#screen .poster').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(first).toBeFocused();
  const left = () => page.evaluate(() => document.activeElement.getBoundingClientRect().left);
  const start = await left();
  for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowRight');
  expect(await row.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  expect(Math.abs(await left() - start)).toBeLessThan(2);
  for (let i = 0; i < 15; i++) await page.keyboard.press('ArrowRight');
  await expect(row.locator('.poster').last()).toBeFocused();
  const atEnd = await row.evaluate(node => [node.scrollLeft, node.scrollWidth - node.clientWidth, document.activeElement.getBoundingClientRect().right, innerWidth]);
  expect(atEnd[0]).toBeGreaterThanOrEqual(atEnd[1] - 1);
  expect(atEnd[2]).toBeLessThanOrEqual(atEnd[3]);
  expect(await left()).toBeGreaterThan(start + 100);
  await page.keyboard.press('ArrowLeft');
  expect(await row.evaluate((node, end) => node.scrollLeft >= end - 1, atEnd[1])).toBe(true);
  for (let i = 0; i < 40; i++) await page.keyboard.press('ArrowLeft');
  await expect(first).toBeFocused();
  expect(await row.evaluate(node => node.scrollLeft)).toBe(0);
});

test('History tab lists watched movies newest first', async ({page}) => {
  await page.locator('[data-tab="history"]').click();
  await expect(page.locator('#screen')).toContainText('No watch history yet');
  await page.evaluate(([a, b]) => {
    localStorage.setItem('movie-world-watch-progress', JSON.stringify([{movie_id:a.id,movie:a,position:5,duration:10},{movie_id:b.id,movie:b,position:5,duration:10}]));
  }, [other, movie]);
  await page.reload();
  await page.locator('[data-tab="history"]').click();
  await expect(page.locator('[data-tab="history"]')).toHaveAttribute('aria-current','page');
  await expect(page.locator('#screen .poster')).toHaveText([other.title, movie.title].map(t => new RegExp(t)));
  await expect(page.locator('#screen .poster').first()).toBeFocused();
});
