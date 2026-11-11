import {test,expect} from '@playwright/test';
import {fixture,overflow,capture} from './helpers';

test.beforeEach(()=>fixture('reset'));

test('everyday entry preserves descriptions and saves unavailable work without starting a model',async({page},info)=>{
 await page.goto('/');await expect(page.getByRole('heading',{name:'Dashboard',exact:true})).toBeVisible();
 await expect(page.getByText('No work running')).toBeVisible();
 await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Backlog',exact:true}).click();
 await expect(page.getByText('No tasks have this status.')).toBeVisible();
 await page.getByRole('link',{name:'Create a task',exact:true}).filter({visible:true}).click();
 await page.getByLabel('Description',{exact:true}).fill('Keep the selected map open after reloading.');
 await page.getByLabel('Type of work').selectOption('inspection');
 await expect(page.getByLabel('Description',{exact:true})).toHaveValue('Keep the selected map open after reloading.');
 await expect(page.getByText('Saved for later, without starting work.')).toBeVisible();
 await page.reload();await expect(page.getByLabel('Type of work')).toHaveValue('inspection');
 await expect(page.getByLabel('Description',{exact:true})).toHaveValue('Keep the selected map open after reloading.');
 await page.getByRole('button',{name:'Save for later',exact:true}).dblclick();
 await expect(page.getByRole('heading',{name:'Saved for later',exact:true})).toBeVisible();
 const saved=await page.request.get('/api/intake').then(r=>r.json());
 expect(saved.total).toBe(1);expect(saved.items[0].original.title).toBe('Keep the selected map open after reloading.');
 expect(saved.items[0].jobs).toHaveLength(0);expect(saved.items[0].decisions).toHaveLength(0);
 await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Backlog',exact:true}).click();
 await page.getByRole('searchbox',{name:'Find a task'}).fill('selected map');
 await expect(page.getByText('1 matching request · 1 saved')).toBeVisible();
 await page.getByRole('combobox',{name:'Task status'}).selectOption('unavailable');
 await expect(page.getByRole('link',{name:/Keep the selected map open/})).toBeVisible();
 await page.reload();await expect(page.getByRole('link',{name:/Keep the selected map open/})).toBeVisible();
 await overflow(page);await capture(page,info,'workshop-saved-future',{keepInCompact:true});
});

test('dashboard decisions reflect recorded receipts without duplicating unanswered approvals',async({page},info)=>{
 await page.goto('/intake/new?kind=feature');
 await page.getByLabel('Description',{exact:true}).fill('Keep the guide controls and selected guide step after a reload.');
 await page.getByLabel('Title Optional').fill('A clear guide plan');
 await page.getByRole('button',{name:'Submit request',exact:true}).click();fixture('planning');
 await page.goto('/');await expect(page.getByRole('link',{name:/A clear guide plan/})).toBeVisible();
 await page.getByRole('link',{name:/A clear guide plan/}).click();
 await page.getByText('Add a note or request changes',{exact:true}).click();
 await page.getByLabel('Note Required when requesting changes').fill('Preserve the current keyboard controls.');
 await page.getByRole('button',{name:'Request changes',exact:true}).click();
 await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
 await page.goto('/');await expect(page.getByText('No questions or approvals are waiting for you.')).toBeVisible();
 await page.goto('/backlog?state=decision_pending');
 await expect(page.locator('.task-state').getByText('Decision recorded',{exact:true})).toBeVisible();
 await expect(page.getByText('Your choice is saved and waiting to apply.')).toBeVisible();
 await overflow(page);await capture(page,info,'workshop-decision-recorded',{keepInCompact:true});
});

test('styled phone and enlarged text keep navigation, task entry and failures usable',async({page},info)=>{
 for(const [width,height,font] of [[390,844,16],[320,568,20],[800,400,32]]){
  await page.setViewportSize({width,height});await page.goto('/');
  await page.evaluate(size=>document.documentElement.style.fontSize=size+'px',font);
  await expect(page.getByRole('heading',{name:'Dashboard',exact:true})).toBeVisible();await overflow(page);
  const create=page.getByRole('link',{name:'Create a task',exact:true}).filter({visible:true});await create.focus();
  expect(await create.evaluate(el=>getComputedStyle(el).outlineStyle)).not.toBe('none');
  await create.press('Enter');await expect(page.getByLabel('Description',{exact:true})).toBeVisible();await overflow(page);
  await capture(page,info,`workshop-entry-${width}-${font}`,{keepInCompact:true});
 }
 await page.route('**/api/workspace?*',route=>route.abort());await page.goto('/');
 await expect(page.getByText('Your tasks could not be loaded')).toBeVisible({timeout:15000});
 await expect(page.getByRole('button',{name:'Try again',exact:true})).toBeVisible();await overflow(page);
});

test('isolated UI state fixtures show activity only for active records and retain readable image fallbacks',async({page},info)=>{
 // Deliberately synthetic display records; no provider acceptance or history mutation.
 const labels={preparing:'Preparing the reviewed work',working:'Working on the change',checking:'Checking the change',paused:'Paused',failed:'Could not finish',interrupted:'Waiting for recovery',queued:'Queued',decision_pending:'Decision recorded'};
 for(const [state,stage] of Object.entries(labels)){
  const active=['preparing','working','checking'].includes(state);
  const row={id:'synthetic-display-only',title:'A deliberately long task name about keeping guide progress after reconnecting and reloading',kind:'bug',state,stage,message:'Synthetic display state, not a provider result.',active,needs_you:false,created_at:'2026-11-09T00:00:00Z',updated_at:'2026-11-09T00:00:00Z'};
  await page.route('**/api/workspace?*',r=>r.fulfill({json:{items:[row],total:1,all_total:1,page:1,page_size:12,counts:{all:1,active:active?1:0,needs_you:0,queued:state==='queued'?1:0},observed_at:'2026-11-09T00:00:00Z',mode:'demo',highlights:{active:active?[row]:[],needs_you:[],completed:[]}}}));
  await page.goto('/');await expect(page.locator('.work-status').getByText(active?'Working':state==='queued'?'Work waiting':'No work running',{exact:true})).toBeVisible();
  if(active)await expect(page.locator('.task-state').getByText(stage,{exact:true})).toBeVisible();
  await overflow(page);await page.unroute('**/api/workspace?*');
 }
 await page.route('**/art/dashboard/*',r=>r.abort());await page.goto('/intake/new');
 await expect(page.getByLabel('Description',{exact:true})).toBeVisible();
 expect(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(18, 14, 11)');
 await overflow(page);await capture(page,info,'workshop-no-texture',{keepInCompact:true});
});

test('a saved revision and dashboard agree about model waiting and automatic recovery',async({page},info)=>{
 // Isolated presentation data; this is not a real-provider acceptance result.
 await page.goto('/intake/new?kind=issue');
 await page.getByLabel('Title Optional').fill('Names appear late in a vendor list');
 await page.getByLabel('Description',{exact:true}).fill('Some rows show a number until the game returns item data.');
 await page.getByRole('button',{name:'Submit request',exact:true}).click();
 await expect(page).toHaveURL(/\/intake\/[a-f0-9-]{36}/);
 const path=new URL(page.url()).pathname;
 const original=await page.request.get('/api'+path).then(r=>r.json());
 let phase='yielding';
 const model=()=>({state:phase,message:phase==='yielding'?'Waiting for local Qwen. Workbench is asking the verified maintenance cycle to yield.':'Your revision is queued. Workbench is reconciling an old reservation; that marker does not prove maintenance is running.',automatic:true,intake_ids:[original.id],maintenance_active:phase==='yielding',reservation_owner_alive:phase==='yielding',reservation_locked:false,observed_at:null});
 const row=()=>({id:original.id,title:original.original.title,kind:'bug',state:'queued',stage:phase==='yielding'?'Making Qwen available':'Waiting for local Qwen',message:model().message,active:false,needs_you:false,created_at:original.created_at,updated_at:original.created_at,provider:'local',result_id:null,result_kind:null});
 await page.route('**/api/workspace?*',r=>r.fulfill({json:{items:[row()],total:1,all_total:1,page:1,page_size:12,counts:{all:1,active:0,queued:1,needs_you:0},mode:'everyday',observed_at:original.created_at,local_model:model(),highlights:{active:[],waiting:[row()],needs_you:[],completed:[]}}}));
 await page.route('**/api'+path,r=>r.fulfill({json:{...original,status:'queued',original:{...original.original,local_planning:'qwen-once'},local_model:model()}}));
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:844});
  await page.goto('/');await expect(page.getByText('Work waiting',{exact:true})).toBeVisible();
  await expect(page.getByText('Ready when you are',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Local model status'})).toContainText('Recovery is automatic');
  await page.getByRole('link',{name:/Names appear late in a vendor list/}).click();
  const progress=page.getByRole('region',{name:'Task progress',exact:true});
  await expect(progress).toContainText('Making Qwen available');
  await expect(progress).toContainText('verified maintenance cycle');
  await page.reload();await expect(progress).toContainText('Recovery is automatic');
  await overflow(page);await capture(page,info,'priority-waiting-task-'+width,{keepInCompact:true});
 }
 phase='reconciling';await page.goto('/');
 await expect(page.getByRole('region',{name:'Local model status'})).toContainText('does not prove maintenance');
 await expect(page.getByText('Work waiting',{exact:true})).toBeVisible();
 expect((await page.request.get('/api'+path).then(r=>r.json())).decisions).toHaveLength(0);
});

test('saved ordinary bug starts investigation without recreating the task or approving a repair',async({page},info)=>{
 await page.goto('/intake/new?kind=issue');
 await page.getByLabel('Title Optional').fill('Vendor items showing item number instead of name for some items');
 await page.getByLabel('Description',{exact:true}).fill('Vendors and Trainers shows Item 4289 for some items while their icon and tooltip are correct. Other item names work.');
 await page.getByRole('button',{name:'Submit request',exact:true}).click();
 await expect(page).toHaveURL(/\/intake\/[a-f0-9-]{36}/);
 const path=new URL(page.url()).pathname;const item=await page.request.get('/api'+path).then(r=>r.json());
 // Isolated API responses exercise the browser only; real transaction/decoder coverage is in backend tests.
 const binding='9'.repeat(64);let writes=0;let posted:any;
 const oldFailure={id:'old-non-dispatch',kind:'planning',revision:1,status:'failed',attempts:2,message:'Old failure retained.',model:null,created_at:item.created_at,finished_at:item.created_at,diagnostics:{category:'unavailable'}};
 let current={...item,status:'waiting_for_information',jobs:[oldFailure],planning_start_binding:binding};
 await page.route('**/api'+path,route=>route.fulfill({json:current}));
 await page.route('**/api'+path+'/investigation',async route=>{
  writes++;posted=route.request().postDataJSON();
  expect(posted.binding).toBe(binding);expect(posted.provider).toBe('qwen-once');
  expect(Object.keys(posted).sort()).toEqual(['binding','command_id','provider']);
  current={...current,status:'queued',jobs:[oldFailure,{...oldFailure,id:'fresh-permission',revision:2,status:'queued',attempts:0,message:'Queued.',finished_at:null}],planning_start_binding:undefined};
  await route.fulfill({json:current});
 });
 await page.reload();await expect(page.getByText('Ready for read-only investigation',{exact:true})).toBeVisible();
 const panel=page.getByRole('region',{name:'Start saved bug investigation'});await expect(panel.getByRole('button',{name:'Start investigation',exact:true})).toBeDisabled();
 await panel.getByLabel('Allow this read-only investigation').check();await panel.getByRole('button',{name:'Start investigation',exact:true}).dblclick();
 await expect(page.getByText('Waiting to start',{exact:true})).toBeVisible();expect(writes).toBe(1);expect(posted.command_id).toMatch(/^[a-f0-9-]{36}$/);
 await expect(page.getByRole('heading',{name:'Why planning stopped',exact:true})).toHaveCount(0);
 await page.reload();await expect(page.getByRole('button',{name:'Start investigation',exact:true})).toHaveCount(0);
 expect(new URL(page.url()).pathname).toBe(path);expect(item.decisions).toHaveLength(0);
 await page.setViewportSize({width:390,height:844});await overflow(page);await capture(page,info,'saved-report-investigation-recorded',{keepInCompact:true});
});

test('pause keeps a new request queued and resume starts that same saved request',async({page},info)=>{
 await page.setViewportSize({width:390,height:844});
 await page.goto('/settings');
 await page.getByRole('button',{name:'Pause new work',exact:true}).click();
 await expect(page.getByRole('button',{name:'Resume work',exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'Work controls'})).toContainText('Work already running can finish safely.');
 await page.reload();
 await expect(page.getByRole('button',{name:'Resume work',exact:true})).toBeVisible();
 await page.goto('/intake/new?kind=issue');
 await page.getByLabel('Title Optional').fill('A saved request while paused');
 await page.getByLabel('Description',{exact:true}).fill('Controlled browser request. Keep the selected guide step after a reload.');
 await page.getByRole('button',{name:'Submit request',exact:true}).click();
 fixture('task-pr-step');
 const saved=await page.request.get('/api/intake').then(r=>r.json());
 const request=saved.items.find((item:{original:{title:string}})=>item.original.title==='A saved request while paused');
 expect(request.jobs).toHaveLength(1);expect(request.jobs[0].status).toBe('queued');
 await page.goto('/backlog?state=paused');
 await expect(page.getByRole('link',{name:/A saved request while paused/})).toBeVisible();
 await page.getByRole('link',{name:'Resume work in Settings'}).click();
 await page.getByRole('button',{name:'Resume work',exact:true}).click();
 await expect(page.getByRole('button',{name:'Pause new work',exact:true})).toBeVisible();
 fixture('planning');
 await page.goto('/intake/'+request.id);
 await expect(page.getByText('Findings ready',{exact:true})).toBeVisible();
 await page.goto('/settings');
 await page.evaluate(()=>document.documentElement.style.fontSize='200%');
 await overflow(page);
 await capture(page,info,'work-control-phone',{keepInCompact:true});
});

test('result outcome, real draft title and next step stay ahead of expandable evidence',async({page},info)=>{
 // Controlled presentation only. No provider, GitHub write or stored result is created.
 const result={id:'display-result',title:'Vendor items show a number instead of a name',status:'checked',message:'Original checks passed.',base:'base',current_grant:false,grant_id:'grant',expires_at:'2026-11-09T00:00:00Z',publication_allowed:false,source:'real',current_task:true,
  result:{verification:'passed',files:[{path:'addon/items.lua',kind:'modified',truncated:false,diff:'@@ -1 +1 @@\n-old\n+new'}],checks:[{status:'pass',rc:0,out:'Checks passed.'}]}};
 const delivery={status:'published',published:true,source:'github',url:'https://github.com/Twich-Team/TwichUI/pull/10',message:'Draft PR created and confirmed.',repository:'Twich-Team/TwichUI',base_branch:'main',current_eligible:true,current_message:'',preview:{title:'Refresh vendor names when item data arrives',body:'The exact checked changes.',category:'Bug fix'}};
 await page.route('**/api/real-executions/display-result',r=>r.fulfill({json:result}));
 await page.route('**/api/real-executions/display-result/delivery',r=>r.fulfill({json:delivery}));
 for(const [width,font] of [[1440,16],[390,16],[320,32]]){
  await page.setViewportSize({width,height:width<500?844:1000});
  await page.goto('/executions/display-result');
  await page.evaluate(size=>document.documentElement.style.fontSize=size+'px',font);
  await expect(page.getByRole('heading',{name:'Fix prepared — automated checks passed'})).toBeVisible();
  const link=page.getByRole('link',{name:'Review the draft PR on GitHub'});
  await expect(link).toHaveAttribute('href',delivery.url);
  await expect(page.getByText(delivery.preview.title,{exact:true}).first()).toBeVisible();
  await expect(page.getByText(/Next: review the draft on GitHub/)).toBeVisible();
  await expect(page.getByText('Not verified',{exact:true})).toBeVisible();
  await overflow(page);
  await capture(page,info,`result-outcome-${width}-${font}`,{keepInCompact:true});
  if(font===16){const bounds=await link.boundingBox();expect(bounds!.y+bounds!.height).toBeLessThan(width<500?844:1000);}
  const changes=page.getByText(/See the changes in code/);
  await changes.focus();await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{name:'addon/items.lua'})).toBeVisible();
  await page.reload();await expect(page.getByRole('heading',{name:'Fix prepared — automated checks passed'})).toBeVisible();
 }
 await page.route('**/api/real-executions/display-result',r=>r.abort());
 await page.reload();
 await expect(page.getByRole('button',{name:'Try again',exact:true})).toBeVisible();
 await page.unroute('**/api/real-executions/display-result');
 await page.route('**/api/real-executions/display-result',r=>r.fulfill({json:result}));
 await page.getByRole('button',{name:'Try again',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Fix prepared — automated checks passed'})).toBeVisible();
});

test('backlog combines request description, work type and state filters across reload',async({page},info)=>{
 await page.goto('/intake/new');
 await page.getByLabel('Title Optional').fill('Vendor rows');
 await page.getByLabel('Description',{exact:true}).fill('Names arrive late after the vendor window opens.');
 await page.getByRole('button',{name:'Submit request',exact:true}).click();
 await expect(page).toHaveURL(/\/intake\/[a-f0-9-]{36}/);
 await page.goto('/intake/new');
 await page.getByLabel('Type of work').selectOption('inspection');
 await page.getByLabel('Title Optional').fill('Inspect startup');
 await page.getByLabel('Description',{exact:true}).fill('Inspect startup for repeated work.');
 await page.getByRole('button',{name:'Save for later',exact:true}).click();
 await expect(page).toHaveURL(/\/intake\/[a-f0-9-]{36}/);
 await page.goto('/backlog');
 await page.getByLabel('Type of work').selectOption('bug');
 await page.getByRole('searchbox',{name:'Find a task'}).fill('arrive late');
 await expect(page.getByText('1 matching request · 2 saved')).toBeVisible();
 await expect(page.getByRole('link',{name:/Vendor rows/})).toBeVisible();
 await page.reload();
 await expect(page.getByRole('searchbox',{name:'Find a task'})).toHaveValue('arrive late');
 await page.getByLabel('Task status').selectOption('failed');
 await expect(page.getByText('0 matching requests · 2 saved')).toBeVisible();
 await page.getByRole('button',{name:'Clear filters',exact:true}).click();
 await expect(page.getByText('2 matching requests · 2 saved')).toBeVisible();
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.documentElement.style.fontSize='200%');
 await overflow(page);await capture(page,info,'backlog-combined-filters-phone',{keepInCompact:true});
});

test('simple settings persist a real policy and preserve pause while showing honest capacity limits',async({page},info)=>{
 await page.goto('/settings');
 await page.getByRole('button',{name:'Pause new work',exact:true}).click();
 await expect(page.getByRole('button',{name:'Resume work',exact:true})).toBeVisible();
 const session=await page.request.get('/api/session').then(r=>r.json());
 const overview=await page.request.get('/api/models').then(r=>r.json());
 const policy={...overview.policy,pause_new_work:true,providers:{...overview.policy.providers,
  local:{...overview.policy.providers.local,model:'pilot-qwen38-27b',enabled:true},
  codex:{...overview.policy.providers.codex,model:'fixture-codex',enabled:true}}};
 const response=await page.request.put('/api/models/policy',{headers:{origin:'http://127.0.0.1:5380','x-csrf-token':session.csrf_token},data:policy});
 expect(response.ok()).toBeTruthy();await page.reload();
 await page.getByRole('combobox',{name:'Preferred provider',exact:true}).selectOption('codex');
 await page.getByRole('combobox',{name:'Fallback for new task decisions',exact:true}).selectOption('codex-to-qwen');
 await page.getByRole('button',{name:'Save task model preferences',exact:true}).click();
 await expect(page.getByText('Model preferences saved for new task decisions. Existing work keeps its approved provider.')).toBeVisible();
 const saved=await page.request.get('/api/models').then(r=>r.json());
 expect(saved.policy).toMatchObject({preferred:'codex',substitution:'configured_fallback_only',test_fallback:'codex-to-qwen',pause_new_work:true});
 await page.reload();
 await expect(page.getByRole('combobox',{name:'Preferred provider',exact:true})).toHaveValue('codex');
 await expect(page.getByRole('combobox',{name:'Fallback for new task decisions',exact:true})).toHaveValue('codex-to-qwen');
 await expect(page.getByText(/No live subscription balance is shown here/)).toBeVisible();
 for(const [width,font] of [[1440,16],[390,16],[320,32]]){
  await page.setViewportSize({width,height:844});await page.evaluate(size=>document.documentElement.style.fontSize=size+'px',font);
  await overflow(page);await capture(page,info,`simple-models-${width}-${font}`,{keepInCompact:true});
 }
});

test('companion dashboard distinguishes working, waiting, attention and retained failures',async({page},info)=>{
 const now=new Date().toISOString();
 const row={id:'controlled-request',title:'Keep the map at the chosen zoom',kind:'bug',state:'working',stage:'Working on the change',message:'Working in a protected copy.',active:true,needs_you:false,created_at:now,updated_at:now,result_id:null,result_kind:null,provider:'local',priority_manual:false};
 let current:any;
 await page.route('**/api/workspace?*',r=>r.fulfill({json:current}));
 const cases=[
  ['working','Working on the change',true,false],['checking','Checking the change',true,false],
  ['queued','Waiting for local Qwen',false,false],['paused','Paused',false,false],
  ['interrupted','Waiting for recovery',false,false],['failed','Could not finish',false,false],
  ['needs_input','Needs your input',false,true],['needs_approval','Needs approval',false,true],
 ];
 for(const [state,stage,active,needs] of cases){
  const task={...row,state,stage,active,needs_you:needs};
  current={items:[task],total:1,all_total:1,page:1,page_size:12,counts:{all:1,active:active?1:0,queued:state==='queued'?1:0,needs_you:needs?1:0,completed:0},mode:'demo',observed_at:now,work_paused:state==='paused',highlights:{active:active?[task]:[],waiting:state==='queued'?[task]:[],needs_you:needs?[task]:[],completed:[],outcomes:state==='failed'?[task]:[]}};
  await page.goto('/');await expect(page.getByRole('heading',{name:'Dashboard',exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:`${active?1:0} Working`,exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:`${needs?1:0} Need you`,exact:true})).toBeVisible();
  await expect(page.locator('.journey-rail')).toHaveCount(active?1:0);
  if(needs)await expect(page.getByRole('link',{name:/Keep the map at the chosen zoom/})).toContainText(state==='needs_input'?'Answer question':'Review next step');
  await expect(page.locator('.recent-panel')).not.toContainText('Keep the map at the chosen zoom');
  await overflow(page);
 }
 await page.setViewportSize({width:390,height:844});await page.reload();await overflow(page);
 await capture(page,info,'companion-attention-phone',{keepInCompact:true});
 await page.evaluate(()=>document.documentElement.style.fontSize='200%');await overflow(page);
 await page.getByRole('link',{name:/Keep the map at the chosen zoom/}).focus();
 await page.keyboard.press('Enter');await expect(page).toHaveURL(/intake\/controlled-request/);
});

test('backlog ordering stays in the URL and uses the full filtered API scope',async({page})=>{
 await page.goto('/intake/new');await page.getByLabel('Description',{exact:true}).fill('Inspect the map’s initial zoom.');
 await page.getByLabel('Type of work').selectOption('inspection');await page.getByRole('button',{name:'Save for later',exact:true}).click();
 await expect(page).toHaveURL(/intake\/[a-f0-9-]{36}/);
 await page.goto('/backlog');await page.getByLabel('Order by').selectOption('priority');
 await expect(page).toHaveURL(/sort=priority/);await page.reload();
 await expect(page.getByLabel('Order by')).toHaveValue('priority');
 await expect(page.getByRole('link',{name:/Inspect the map’s initial zoom/})).toContainText('Suggested priority');
 await page.getByRole('button',{name:/Working/}).click();
 await expect(page.getByText('No tasks have this status.')).toBeVisible();
 await page.getByRole('button',{name:'Clear filters',exact:true}).click();
 await expect(page.getByRole('link',{name:/Inspect the map’s initial zoom/})).toBeVisible();
});
