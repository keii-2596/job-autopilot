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
 assert.equal(await page.getByText('嵌入式预览', {exact:true}).count(), 0);
 // Exercise the chat UI with a fake transport, without launching a model or an application.
 let runtime={available:true,connected:false,state:'idle',thread_id:'test-thread',project_path:'/test/project'};
 let sentMessage;
 await page.route('**/api/codex',r=>r.fulfill({json:runtime}));
 await page.route('**/api/codex/message',r=>{
  sentMessage=r.request().postDataJSON().message;
  runtime={...runtime,connected:true,state:'running',turn_id:'test-turn',last_agent_message:'已收到消息'};
  return r.fulfill({status:202,json:runtime});
 });
 await page.locator('#refresh-button').click();
 await page.waitForFunction(()=>document.querySelector('#codex-state-title').textContent==='尚未运行');
 assert.equal(await page.locator('.codex-connection').innerText(),'Codex 按需连接');
 assert(await page.locator('#codex-quick-authorize').isDisabled());
 await page.locator('#codex-chat-input').fill('帮我筛选前端岗位');
 await page.locator('#codex-send-button').click();
 await page.waitForFunction(()=>document.querySelector('#codex-send-button').textContent==='追加指令');
 assert.equal(sentMessage,'帮我筛选前端岗位');
 assert.equal(await page.locator('#codex-chat').getAttribute('data-state'),'running');
 assert.equal(await page.locator('#codex-state-title').innerText(),'正在运行');
 await page.locator('#codex-chat-input').fill('保留我的草稿');
 await page.getByRole('button',{name:'帮我投简历',exact:true}).click();
 assert.equal(sentMessage,'帮我投简历');
 assert.equal(await page.locator('#codex-chat-input').inputValue(),'保留我的草稿');
 runtime={...runtime,state:'awaiting_input',pending_request:{id:'input-1',kind:'user_input',message:'期望地点？',params:{questions:[{id:'city',question:'期望地点？'}]}}};
 await page.locator('#refresh-button').click();
 await page.locator('#approval-form input[name=city]').fill('上海');
 await page.waitForResponse(r=>new URL(r.url()).pathname==='/api/codex');
 assert.equal(await page.locator('#approval-form input[name=city]').inputValue(),'上海');
 assert.equal(await page.locator('#codex-state-title').innerText(),'等待你的确认');
 assert(await page.locator('#codex-quick-authorize').isDisabled());
 assert(await page.getByRole('button',{name:'继续',exact:true}).isDisabled());
 let authorization;
 await page.route('**/api/codex/respond',r=>{
  authorization=r.request().postDataJSON();
  runtime={...runtime,state:'running',pending_request:null};
  return r.fulfill({json:runtime});
 });
 runtime={...runtime,state:'awaiting_input',pending_request:{id:'permission-1',kind:'permissions',message:'仅读取所选简历',params:{permissions:{fileSystem:{read:['/test/resume.txt']}}}}};
 await page.locator('#refresh-button').click();
 await page.locator('#codex-quick-authorize').click();
 assert.equal(authorization.request_id,'permission-1');
 assert.equal(authorization.decision,'accept');
 await page.route('**/api/codex/release',r=>{
  runtime={...runtime,state:'released',connected:false,pending_request:null,turn_id:''};
  return r.fulfill({json:runtime});
 });
 await page.locator('#codex-release-button').click();
 await page.waitForFunction(()=>document.querySelector('#codex-release-button').disabled);
 await page.locator('#codex-approval').waitFor({state:'hidden'});
 await page.waitForFunction(()=>document.querySelector('#codex-state-title').textContent==='已移交桌面');
 await page.getByRole('button',{name:'继续',exact:true}).click();
 assert.equal(sentMessage,'继续');
 await page.locator('#codex-release-button').click();
 await page.locator('[data-view="profile"]').click();
 await page.locator('#allowed-domains').fill('*');
 await page.locator('#save-policy').click();
 await page.locator('#policy-check-url').fill('https://jobs.example.com/role');
 await page.locator('#policy-check-form button').click();
 await page.waitForFunction(()=>document.querySelector('#policy-check-result').textContent.includes('匹配 *'));
 const projectPath=await page.locator('#codex-project-path').inputValue();
 assert(projectPath.includes('job-autopilot-e2e-'));
 await page.route('**/api/codex/projects',r=>r.fulfill({json:[{id:'test-project',name:'求职测试项目',roots:[{path:projectPath}]}]}));
 await page.locator('#load-codex-projects').click();
 await page.locator('#codex-project-picker').selectOption({label:'求职测试项目'});
 assert.equal(await page.locator('#codex-project-path').inputValue(),projectPath);
 await page.locator('#codex-project-form button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('#codex-project-result').textContent.includes('已保存'));
 await page.locator('[data-view="jobs"]').click();
 await page.locator('#source-job-direction').selectOption('frontend');
 await page.waitForResponse(r=>r.url().includes('/api/source-jobs?')&&r.url().includes('direction=frontend'));
 await page.locator('[data-view="overview"]').click();
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
 await page.locator('[data-view="overview"]').click();
 assert(await page.locator('#codex-send-button').isVisible());
 const overflow=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,elements:[...document.querySelectorAll('#view-overview *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,12)}));
 assert(overflow.scroll<=overflow.width,JSON.stringify(overflow));
 if (process.env.SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, 'conversation-390.png'), animations:'disabled'});
 await page.locator('[data-view="applications"]').click();
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
 await page.unroute('**/api/applications');
 // Automatic recovery can hide the button before the click; either recovery path is valid.
 await page.locator('#retry-connection').click({timeout:1000}).catch(async error=>{
  if(await page.locator('#connection-error').isVisible()) throw error;
 });
 await page.locator('#connection-error').waitFor({state:'hidden'});
 // Public job sync has its own progress and opt-in; no real network/DB merge here.
 let librarySync={state:'idle',auto_sync:false,message:'尚未同步'};
 await page.route('**/api/job-library-sync',r=>r.fulfill({json:librarySync}));
 await page.route('**/api/job-library-sync/settings',r=>{
  assert.equal(r.request().headers()['x-job-autopilot'],'dashboard');
  librarySync={...librarySync,auto_sync:r.request().postDataJSON().auto_sync};
  return r.fulfill({json:librarySync});
 });
 await page.route('**/api/job-library-sync/start',r=>{
  assert.equal(r.request().headers()['x-job-autopilot'],'dashboard');
  librarySync={...librarySync,state:'checking',message:'正在检查 GitHub 职位数据版本'};
  return r.fulfill({status:202,json:librarySync});
 });
 await page.locator('[data-view="jobs"]').click();
 await page.locator('#source-job-direction').selectOption('frontend');
 await page.waitForResponse(r=>r.url().includes('/api/source-jobs?')&&r.url().includes('direction=frontend'));
 await page.locator('#sync-github-jobs').click();
 await page.waitForFunction(()=>document.querySelector('#sync-github-jobs').textContent==='正在同步…');
 assert.equal(await page.locator('#sync-github-jobs').innerText(),'正在同步…');
 librarySync={...librarySync,state:'merging',message:'正在合并新增和变更项'};
 await page.waitForFunction(()=>document.querySelector('#github-jobs-sync-message').textContent.includes('正在合并'));
 const refreshedJobs=page.waitForResponse(r=>r.url().includes('/api/source-jobs?'));
 librarySync={...librarySync,state:'completed',synced_commit:'b'.repeat(40),checked_at:'2026-09-08T09:00:00Z',message:'同步完成：新增 2，更新 1，未变 10，保留本地内容 1。'};
 await refreshedJobs;
 await page.waitForFunction(()=>!document.querySelector('#sync-github-jobs').disabled);
 assert.equal(await page.locator('#source-job-direction').inputValue(),'frontend');
 assert((await page.locator('#github-jobs-sync-version').innerText()).includes('bbbbbbbb'));
 await page.locator('#auto-sync-github-jobs').check();
 await page.waitForFunction(()=>!document.querySelector('#auto-sync-github-jobs').disabled);
 assert.equal(librarySync.auto_sync,true);
 assert.equal(await page.locator('#auto-update').isChecked(),false);
 for(const width of [1440,390]) {
  await page.setViewportSize({width,height:1000});
  await page.locator('.library-sync').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(process.env.SCREENSHOT_DIR) await page.locator('.library-sync').screenshot({path:path.join(process.env.SCREENSHOT_DIR,`job-sync-${width}.png`),animations:'disabled'});
 }
 await page.locator('#auto-sync-github-jobs').uncheck();
 await page.locator('#sync-github-jobs').click();
 await page.waitForFunction(()=>document.querySelector('#sync-github-jobs').textContent==='正在同步…');
 librarySync={...librarySync,state:'failed',message:'职位同步失败：示例网络错误'};
 await page.waitForFunction(()=>document.querySelector('#github-jobs-sync-message').textContent.includes('示例网络错误'));
 assert(await page.locator('#sync-github-jobs').isEnabled());
 // Update UI uses an isolated remote transport and never installs into the user's plugin.
 let updates=await (await page.request.get(baseURL+'/api/updates')).json();
 await page.route('**/api/updates',r=>r.fulfill({json:updates}));
 await page.route('**/api/updates/check',r=>{
  assert.equal(r.request().headers()['x-job-autopilot'],'dashboard');
  updates={...updates,state:'available',available:true,latest_commit:'a'.repeat(40),release_note:'新增更新功能 <script>not executable</script>',message:'发现远程新提交，可以安装'};
  return r.fulfill({status:202,json:updates});
 });
 await page.route('**/api/updates/settings',r=>{
  updates={...updates,auto_update:r.request().postDataJSON().auto_update};
  return r.fulfill({json:updates});
 });
 await page.route('**/api/updates/install',r=>{
  updates={...updates,state:'waiting',message:'任务正在运行，未中断；空闲后再安装更新。'};
  return r.fulfill({status:202,json:updates});
 });
 await page.locator('[data-view="updates"]').click();
 await page.locator('#check-updates').click();
 await page.waitForFunction(()=>document.querySelector('#update-state').textContent==='有可用更新');
 assert.equal(await page.locator('#update-release-note script').count(),0);
 assert(await page.locator('#install-update').isEnabled());
 await page.locator('#auto-update').check();
 await page.waitForResponse(r=>r.url().endsWith('/api/updates') || r.url().endsWith('/api/updates/settings'));
 assert.equal(updates.auto_update,true);
 await page.locator('#install-update').click();
 await page.waitForFunction(()=>document.querySelector('#update-state').textContent==='等待任务空闲');
 await page.locator('#auto-update').uncheck();
 const externalEntry={kind:'unverified_activity',run_id:'old-activity',revision:17,label:'旧投递任务 <script>not executable</script>',detail:'无法仅凭记录确认是否仍在执行；请先核实原对话',updated_at:'2026-09-08T07:58:48Z',clearable:true};
 updates={...updates,state:'needs_review',message:'需要核实历史任务，并非当前网页任务仍在运行',task_guard:{blocked:true,blockers:[externalEntry],stale_web:[]}};
 await page.waitForFunction(()=>document.querySelector('#update-state').textContent==='核实历史任务');
 assert(await page.locator('#install-update').isDisabled());
 assert.equal(await page.locator('#update-task-guard script').count(),0);
 let reconcileCalls=0;
 await page.route('**/api/updates/reconcile',r=>{
  reconcileCalls++;
  assert.equal(r.request().headers()['x-job-autopilot'],'dashboard');
  assert.deepEqual(r.request().postDataJSON(),{entries:[{run_id:'old-activity',revision:17}],confirmed:true});
  updates={...updates,state:'available',message:'当前没有任务阻挡，可以安装；无需连接 Codex',reconciled:1,task_guard:{blocked:false,blockers:[],stale_web:[]}};
  return r.fulfill({json:updates});
 });
 for(const width of [1440,390]) {
  await page.setViewportSize({width,height:1100});
  await page.locator('#update-panel').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(process.env.SCREENSHOT_DIR) await page.locator('#update-panel').screenshot({path:path.join(process.env.SCREENSHOT_DIR,`updates-${width}.png`),animations:'disabled'});
 }
 page.once('dialog',dialog=>dialog.dismiss());
 await page.getByRole('button',{name:'确认原任务已停止',exact:true}).click();
 assert.equal(reconcileCalls,0);
 page.once('dialog',dialog=>dialog.accept());
 await page.getByRole('button',{name:'确认原任务已停止',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('#install-update').disabled);
 assert.equal(reconcileCalls,1);
 assert(await page.locator('#update-task-guard').isHidden());
 updates={...updates,state:'waiting',task_guard:{blocked:true,blockers:[{kind:'codex',label:'Codex 正在等待回答 · 当前投递任务',detail:'处理确认或暂停后再更新',clearable:false}],stale_web:[]}};
 await page.waitForFunction(()=>document.querySelector('#install-update').disabled);
 assert.equal(await page.getByRole('button',{name:'确认原任务已停止',exact:true}).count(),0);
 await page.getByRole('button',{name:'查看当前任务',exact:true}).click();
 assert(await page.locator('#codex-chat').isVisible());
 updates={...updates,state:'available',task_guard:{blocked:false,blockers:[],stale_web:[]}};
 await page.goto(baseURL+'/?view=updates');
 await page.waitForFunction(()=>!document.querySelector('#install-update').disabled);
 assert(await page.locator('#update-panel').isVisible());
 // Native EventSource, served in short replayable frames by a fake model transport.
 let profileImport={request_id:'resume-ui-1',status:'running',resume_path:'/test/resume.txt',message:'正在理解简历',updated_at:'2026-09-08T08:00:00Z',suggestions:[],agent_messages:[{id:'message-1',text:'正在读取'}],progress_events:[{status:'running',message:'Codex 已接管',at:'2026-09-08T08:00:00Z'}]};
 let streamOffline=false;
 await page.route('**/api/profile/import',r=>r.fulfill({json:profileImport}));
 await page.route('**/api/profile/import/events',r=>streamOffline ? r.abort() : r.fulfill({contentType:'text/event-stream',body:`retry: 100\nevent: profile\ndata: ${JSON.stringify(profileImport)}\n\n`}));
 runtime={...runtime,state:'running',connected:true,resume_request_id:'resume-ui-1',pending_request:null};
 await page.goto(baseURL); await page.locator('#metrics .metric').first().waitFor();
 await page.locator('[data-view="profile"]').click();
 await page.waitForFunction(()=>document.querySelector('#resume-stream').textContent==='正在读取');
 assert(await page.locator('#parse-resume').isDisabled());
 profileImport.agent_messages[0].text+='，已识别教育背景';
 await page.waitForFunction(()=>document.querySelector('#resume-stream').textContent.includes('教育背景'));
 runtime={...runtime,state:'awaiting_input',pending_request:{id:'resume-permission',kind:'permissions',message:'读取简历',params:{}}};
 profileImport.status='awaiting_input';
 await page.waitForFunction(()=>!document.querySelector('#resume-show-approval').hidden);
 await page.locator('#resume-show-approval').click();
 assert(await page.locator('#codex-approval').isVisible());
 await page.locator('[data-view="profile"]').click();
 runtime={...runtime,state:'completed',connected:false,pending_request:null};
 profileImport={...profileImport,status:'ready_for_review',suggestions:[{id:'field-1',label:'姓名',value:'示例候选人'},{id:'field-2',label:'专业',value:'计算机'}]};
 await page.locator('.suggestion-card').first().waitFor();
 await page.locator('.suggestion-card input').first().uncheck();
 profileImport.agent_messages.push({id:'message-2',text:'提取完成，请确认'});
 await page.waitForFunction(()=>document.querySelector('#resume-stream').textContent.includes('请确认'));
 assert.equal(await page.locator('.suggestion-card input').first().isChecked(),false);
 streamOffline=true;
 profileImport={...profileImport,status:'paused',message:'解析已暂停'};
 await page.waitForFunction(()=>document.querySelector('#resume-progress-title').textContent==='解析已暂停');
 assert((await page.locator('#resume-stream').innerText()).includes('教育背景'));
 streamOffline=false;
 profileImport={...profileImport,status:'failed',message:'示例连接错误，可重试'};
 await page.waitForFunction(()=>document.querySelector('#resume-progress-title').textContent==='解析未完成');
 for(const width of [1440,390]) {
  await page.setViewportSize({width,height:1000});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  assert.equal(overflow,false);
  if(process.env.SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,`resume-stream-${width}.png`),animations:'disabled'});
 }
 assert.deepEqual(errors,[]);
 console.log('PASS: dialog close/validation/Escape/backdrop, preferences, detail edit, archive/restore, pending filter, 4 viewport widths, long titles, connection recovery; no JS errors');
 await page.goto(baseURL + '/embedded');
 await page.waitForFunction(()=>document.querySelector('#start-form [name=locations]').value==='上海, 杭州');
 assert.deepEqual(errors,[]);
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{
 if (browser) await browser.close();
 if (fixture) fixture.kill('SIGINT');
});
