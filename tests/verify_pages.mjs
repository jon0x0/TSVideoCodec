// Serve the built artifact beneath its real project prefix and exercise the UI.
// Usage: node tests/verify_pages.mjs [https://host/TSVideoCodec/]
import {createRequire} from 'node:module';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const {chromium}=createRequire(import.meta.url)('playwright');
let server;
let url=process.argv[2];
if(!url){
  const root=path.resolve('build/pages');
  const types={'.js':'text/javascript','.html':'text/html','.css':'text/css','.json':'application/json'};
  server=http.createServer((req,res)=>{
    const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
    const file=path.resolve(root,'.'+pathname.replace(/^\/TSVideoCodec/,''));
    if(!pathname.startsWith('/TSVideoCodec/')||(file!==root&&!file.startsWith(root+path.sep))){
      res.writeHead(404).end();return;
    }
    const target=pathname.endsWith('/')?path.join(file,'index.html'):file;
    if(!fs.existsSync(target)){res.writeHead(404).end();return;}
    res.setHeader('Content-Type',types[path.extname(target)]||'application/octet-stream');
    fs.createReadStream(target).pipe(res);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  url=`http://127.0.0.1:${server.address().port}/TSVideoCodec/`;
}
let browser;
try{
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||undefined,args:['--enable-unsafe-swiftshader']});
  const page=await browser.newPage();
  const errors=[];
  page.on('pageerror',error=>{if(!errors.includes(error.stack))errors.push(error.stack);});
  page.on('response',response=>{if(response.status()>=400)errors.push(`${response.status()} ${response.url()}`);});
  // The deployed player must work when upstream hosts are unavailable.
  await page.route('**/*',route=>{
    if(new URL(route.request().url()).origin!==new URL(url).origin){
      errors.push(`External runtime request: ${route.request().url()}`);
      return route.abort();
    }
    return route.continue();
  });
  await page.goto(url);
  await page.locator('#start:enabled').waitFor();
  await page.locator('#start').click();
  const frame=page.frames().find(frame=>frame.url().includes('/emulator/'));
  for(const demo of ['boing','juggler','newton']){
    await page.locator(`[data-demo="${demo}"]`).click();
    await page.waitForFunction(id=>document.querySelector('#status').textContent===`Running · ${{boing:'Boing Ball',juggler:'Juggler',newton:'Newton'}[id]}`,demo);
    await frame.waitForFunction(()=>window.tsVideoDemo.diagnostics().audioState==='running');
    // TAP may still be loading; wait for stable demo output through the core test
    // and compare real displayed pixels here after the browser loader finishes.
    await page.waitForTimeout(demo==='newton'?10000:1000);
    const before=await frame.evaluate(()=>window.tsVideoDemo.diagnostics());
    await page.waitForTimeout(1200);
    const after=await frame.evaluate(()=>window.tsVideoDemo.diagnostics());
    assert.deepEqual(errors,[],'Browser errors or runtime network dependencies');
    assert.notDeepEqual(after.pixels,before.pixels,`${demo}: browser display stopped animating`);
    assert.ok(new Set(after.pixels).size>2,`${demo}: blank display`);
    assert.equal(await page.locator('#error').textContent(),'');
    await page.locator('#reset').click();
    console.log(`${demo}: browser start, switch, animation, audio and reset passed`);
  }
  await page.goto(new URL('?demo=juggler',url).href);
  await page.waitForFunction(()=>document.querySelector('[data-demo="juggler"]').getAttribute('aria-pressed')==='true');
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='Running · Juggler');
  assert.deepEqual(errors,[],'Browser errors or runtime network dependencies');
}finally{
  await browser?.close();
  if(server)await new Promise(resolve=>server.close(resolve));
}
