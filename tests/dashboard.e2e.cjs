const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const root = path.resolve(__dirname, '..');
let browser, fixture;
(async()=>{
 fixture = spawn(process.env.PYTHON || 'python3', ['-u', path.join(__dirname, 'dashboard_fixture.py')], {
  cwd: root, env: {...process.env, PYTHONPATH: path.join(root, 'runtime')}, stdio: ['ignore', 'pipe', 'inherit']
 });
 const baseURL = await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error('Fixture startup timeout')), 15000);
  fixture.once('error', reject);
  fixture.once('exit', code => reject(new Error(`Fixture exited: ${code}`)));
  readline.createInterface({input: fixture.stdout}).on('line', line => {
   if (line.startsWith('E2E_URL=')) { clearTimeout(timeout); resolve(line.slice(8)); }
  });
 });
 browser=await chromium.launch({headless:true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(baseURL);
 await page.locator('#metrics .metric').first().waitFor();
 for(const [button,dialog] of [['#add-button','#application-dialog'],['#autopilot-button','#run-dialog']]) {
  await page.locator(button).click();
  await page.locator(dialog+' [data-close-dialog]').click();
  assert.equal(await page.locator(dialog).evaluate(x=>x.open),false);
 }
 await page.locator('#add-button').click(); await page.locator('#application-form button.primary').click();
 assert.equal(await page.locator('#application-form').evaluate(x=>x.checkValidity()),false);
 await page.locator('#application-dialog [data-close-dialog]').click();
 assert.equal(await page.locator('#application-dialog').evaluate(x=>x.open),false);
 await page.locator('#add-button').click(); await page.keyboard.press('Escape');
 assert.equal(await page.locator('#application-dialog').evaluate(x=>x.open),false);
 await page.locator('#add-button').click(); await page.mouse.click(5,5);
 assert.equal(await page.locator('#application-dialog').evaluate(x=>x.open),false);
 await page.locator('[data-view="profile"]').click();
 assert.equal(await page.locator('#preferences-form [name=locations]').inputValue(),'上海, 杭州');
 await page.locator('#add-field').click(); await page.locator('#field-dialog [data-close-dialog]').click();
 assert.equal(await page.locator('#field-dialog').evaluate(x=>x.open),false);
 await page.locator('#preferences-form [name=excluded_keywords]').fill('算法, AI Infra, 模型训练');
 await page.locator('#preferences-form button[type=submit]').click();
 await page.reload(); await page.locator('#metrics .metric').first().waitFor();
 await page.locator('#autopilot-button').click();
 assert.equal(await page.locator('#run-form [name=locations]').inputValue(),'上海, 杭州');
 await page.locator('#run-dialog [data-close-dialog]').click();
 await page.locator('[data-view="applications"]').click();
 assert.equal(await page.locator('.role-title[data-id="1"]').innerText(),'AI全栈工程师（上海）');
 await page.locator('.edit-application[data-id="1"]').click();
 await page.locator('#detail-form [name=actual_title]').fill('AI应用开发工程师');
 await page.locator('#detail-form button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('.role-title[data-id="1"]')?.textContent==='AI应用开发工程师');
 await page.locator('.archive-application[data-id="1"]').click();
 await page.waitForFunction(()=>document.querySelectorAll('#applications-table tr').length===3);
 await page.locator('[data-status=archived]').click();
 assert.equal(await page.locator('#applications-table tr').count(),1);
 await page.locator('.archive-application[data-id="1"]').click();
 await page.locator('[data-status=pending]').click();
 await page.waitForFunction(()=>document.querySelectorAll('#applications-table select').length===2);
 await page.locator('[data-status=""]').click();
 for(const width of [1440,1024,768,390]) {
  await page.setViewportSize({width,height:900});
  const sizes=await page.evaluate(()=>({w:innerWidth,scroll:document.documentElement.scrollWidth,selects:[...document.querySelectorAll('#applications-table select')].map(s=>({w:s.offsetWidth,text:s.selectedOptions[0].textContent,font:getComputedStyle(s).font}))}));
  assert(sizes.scroll<=width,JSON.stringify(sizes));
  assert(sizes.selects.every(x=>x.w>=100));
  if (process.env.SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, `applications-${width}.png`), animations:'disabled', style:'#toast { visibility: hidden; }'});
 }
 await page.setViewportSize({width:1440,height:1000});
 await page.route('**/api/source-jobs?*',r=>r.fulfill({json:[{id:1,title:'工程技术类、软件开发、AI应用开发、物资管理、材料学、土木工程、产品经理、数据科学'.repeat(20),company_name:'示例公司',locations:['上海','杭州'],recruitment_batches:['秋招'],education_levels:['硕士'],apply_url:'https://example.com/jobs',source_updated_at:'2026-09-07T00:00:00Z'}]}));
 await page.locator('#refresh-button').click(); await page.locator('[data-view="jobs"]').click();
 await page.locator('.source-title').waitFor();
 assert((await page.locator('#source-jobs-table tr').first().boundingBox()).height<180);
 await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished)));
 assert.equal(await page.locator('#view-jobs').evaluate(e => getComputedStyle(e).opacity), '1');
 if (process.env.SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'jobs-1440.png'), animations:'disabled', style:'#toast { visibility: hidden; }'});
 await page.locator('.source-title summary').click();
 assert((await page.locator('#source-jobs-table tr').first().boundingBox()).height>180);
 await page.route('**/api/applications',r=>r.abort()); await page.locator('#refresh-button').click();
 await page.locator('#connection-error').waitFor({state:'visible'});
 assert.equal(await page.locator('#applications-table select').count(),4);
 await page.unroute('**/api/applications'); await page.locator('#retry-connection').click();
 await page.locator('#connection-error').waitFor({state:'hidden'});
 assert.deepEqual(errors,[]);
 console.log('PASS: dialog close/validation/Escape/backdrop, preferences, detail edit, archive/restore, pending filter, 4 viewport widths, long titles, connection recovery; no JS errors');
 await page.goto(baseURL + '/embedded');
 await page.waitForFunction(()=>document.querySelector('#start-form [name=locations]').value==='上海, 杭州');
 assert.deepEqual(errors,[]);
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{
 if (browser) await browser.close();
 if (fixture) fixture.kill('SIGINT');
});
