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
  await page.goto('/');await expect(page.locator('.work-status').getByText(active?'Working':'No work running',{exact:true})).toBeVisible();
  if(active)await expect(page.locator('.task-state').getByText(stage,{exact:true})).toBeVisible();
  await overflow(page);await page.unroute('**/api/workspace?*');
 }
 await page.route('**/art/dashboard/*',r=>r.abort());await page.goto('/intake/new');
 await expect(page.getByLabel('Description',{exact:true})).toBeVisible();
 expect(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(22, 15, 11)');
 await overflow(page);await capture(page,info,'workshop-no-texture',{keepInCompact:true});
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
