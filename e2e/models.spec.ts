import {artifactPath} from './output';
import { test, expect, type Page } from '@playwright/test';
import { fixture, overflow } from './helpers';
import { mkdirSync } from 'node:fs';

test.beforeEach(({page})=>{fixture('reset');page.on('dialog',dialog=>void dialog.accept());});
async function manual(page:Page,backend:string,value:string){
  await page.getByLabel('Configure provider').selectOption(backend);
  await page.locator('.manual-model summary').click();
  await page.getByLabel('Manual model identifier ·').fill(value);
}
async function routing(page:Page){
  await page.locator('.model-routing-settings > summary').click();
}
async function previewTool(page:Page){await page.locator('.model-preview-tool > summary').click();}
async function shot(page:Page,name:string,selector?:string){
  mkdirSync(artifactPath('m4-ux/screenshots'),{recursive:true});
  if(selector)await page.locator(selector).first().scrollIntoViewIfNeeded();else await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:artifactPath(`m4-ux/screenshots/${name}.png`)});await overflow(page);
}
async function catalog(page:Page,stale=false){
  await page.route('**/api/models',async route=>{
    const response=await route.fetch();const data=await response.json();
    data.capabilities.executors=data.capabilities.executors.map((e:Record<string,unknown>)=>e.backend==='codex' ? {...e,models:[{model:'fixture-codex',efforts:['low','high']}],observed_at:stale?'2000-01-01T00:00:00Z':new Date().toISOString(),default_model:'fixture-codex',default_effort:'high',source:'Synthetic acceptance catalog'} : e);
    if(stale){data.policy.providers.codex.model='fixture-codex';data.policy.providers.codex.effort='high';data.observations[1]={backend:'codex',availability:'unavailable',observed_at:'2000-01-01T00:00:00Z',capacity:'unknown',capability:'unknown',source:'Synthetic stale observation'};}
    await route.fulfill({response,json:data});
  });
}

test('saved policy stays inactive, drafts and conflicts survive overview/back navigation',async({page})=>{
  await page.goto('/models');
  await expect(page.getByRole('heading',{name:'Models',exact:true})).toBeVisible();
  await expect(page.locator('#model-configuration')).toHaveCount(0);
  await page.getByRole('link',{name:'Configure models',exact:true}).click();
  await routing(page);
  await page.getByRole('combobox',{name:'Preferred backend',exact:true}).selectOption('claude');
  await manual(page,'claude','explicit-unverified-model');
  await page.getByRole('link',{name:'← Back to Models'}).click();
  await expect(page.getByText('Unsaved model preferences are kept.',{exact:false})).toBeVisible();
  await page.getByRole('link',{name:'Continue editing'}).click();
  await expect(page.getByLabel('Preferred model · Claude')).toHaveValue('explicit-unverified-model');
  await page.getByRole('link',{name:'Settings',exact:true}).click();
  await page.getByRole('link',{name:'Provider availability and activity',exact:true}).click();
  await page.getByRole('link',{name:'Continue editing'}).click();
  await routing(page);
  await expect(page.getByRole('combobox',{name:'Preferred backend',exact:true})).toHaveValue('claude');
  const before=await(await page.request.get('/api/models')).json();
  const session=await(await page.request.get('/api/session')).json();
  const headers={origin:'http://127.0.0.1:5380','x-csrf-token':session.csrf_token};
  expect((await page.request.put('/api/models/policy',{headers,data:{...before.policy,when_unavailable:'block'}})).ok()).toBe(true);
  await page.getByRole('button',{name:'Save model preferences'}).click();
  await expect(page.getByText(/Model preferences changed/)).toBeVisible();
  await expect(page.getByLabel('Preferred model · Claude')).toHaveValue('explicit-unverified-model');
  await page.getByRole('button',{name:'Discard edits and reload'}).click();
  await expect(page.getByText('Saved preferences reloaded.',{exact:true})).toBeVisible();
  await manual(page,'codex','explicit-unverified-model');
  await expect(page.getByLabel('Reasoning effort · Codex').locator('option')).toHaveCount(1);
  await page.getByRole('button',{name:'Save model preferences'}).click();
  await expect(page.getByText('Configuration saved; not activated.',{exact:true})).toBeVisible();
  await page.reload();await page.getByLabel('Configure provider').selectOption('codex');
  await expect(page.getByLabel('Preferred model · Codex')).toHaveValue('explicit-unverified-model');
  const after=await(await page.request.get('/api/models')).json();
  expect(after.acknowledged_revision).toBeNull();
  expect(Object.values(after.policy.providers).every(p=>(p as {activated:boolean}).activated===false)).toBe(true);
  expect((await(await page.request.get('/api/models/activity')).json()).attempts).toBe(0);
});

test('provider fallback and within-provider substitution are distinct; previews write nothing',async({page})=>{
  await page.goto('/models/configure');await routing(page);
  await expect(page.getByRole('combobox',{name:'Provider fallback',exact:true})).toHaveValue('deny');
  await expect(page.getByText(/Fallback order is saved but will not be used/)).toBeVisible();
  await expect(page.getByText(/Model substitution within a provider is not allowed/)).toBeVisible();
  await page.getByRole('button',{name:'Add Claude fallback'}).click();
  await page.getByRole('button',{name:'Move Claude up'}).click();
  await expect(page.locator('.fallback-order li').first()).toContainText('Claude');
  await page.getByRole('button',{name:'Move Claude down'}).click();
  await page.getByRole('combobox',{name:'Provider fallback',exact:true}).selectOption('configured_fallback_only');
  await expect(page.getByText(/Fallback order is saved but will not be used/)).toHaveCount(0);
  await page.getByRole('button',{name:'Save model preferences'}).click();
  await expect(page.getByText('Configuration saved; not activated.',{exact:true})).toBeVisible();
  expect((await(await page.request.get('/api/models')).json()).policy.substitution).toBe('configured_fallback_only');
  await previewTool(page);
  let writes=0;page.on('request',r=>{if(r.method()!=='GET')writes++;});
  const before=await(await page.request.get('/api/models/activity')).json();
  for(const key of ['fallback','capacity','disabled','stale','unknown','verification','limit']){
    await page.getByLabel('Preview case').selectOption(key);await page.getByRole('button',{name:'Preview routing',exact:true}).click();
    await expect(page.locator('.routing-result')).toContainText('Execution permission: held.');
    if(key==='fallback')await expect(page.locator('.routing-result h3')).toContainText('Codex');
  }
  expect(writes).toBe(0);expect(await(await page.request.get('/api/models/activity')).json()).toEqual(before);
});

for(const width of [1440,390])test(`overview demo focused configuration and preview fit ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:width===390?844:1000});await catalog(page);
  await page.goto('/models');await expect(page.getByText('No provider attempts recorded yet.')).toBeVisible();
  await shot(page,`overview-empty-${width}`);
  await page.getByLabel('Activity view').selectOption('demo');
  await expect(page.getByText('3 distinct tasks · 6 synthetic attempts',{exact:true})).toBeVisible();
  await expect(page.getByRole('figure')).toHaveCount(3);
  await expect(page.getByText(/5 of 6 attempts have recorded end dates/)).toBeVisible();
  await expect(page.getByText(/1 demo attempt has no recorded end time/)).toBeVisible();
  await expect(page.locator('.token-observation meter')).toHaveCount(0);
  await shot(page,`demo-${width}`,'.model-activity');
  await page.locator('.model-activity > details').last().click();
  await expect(page.getByText(/Synthetic escalation from local/)).toBeVisible();
  await expect(page.getByText(/actual model and end time remain unknown/)).toBeVisible();
  await page.getByLabel('Activity view').selectOption('observed');await expect(page.getByText('No provider attempts recorded yet.')).toBeVisible();
  await page.getByRole('link',{name:'Configure Codex',exact:true}).click();
  await expect(page.getByLabel('Preferred model · Codex').locator('option')).toHaveCount(2);
  await page.getByLabel('Preferred model · Codex').selectOption('fixture-codex');
  await expect(page.getByLabel('Reasoning effort · Codex').locator('option')).toHaveCount(3);
  await page.getByRole('button',{name:'Copy installed preference'}).click();await expect(page.getByLabel('Reasoning effort · Codex')).toHaveValue('high');
  await shot(page,`configuration-${width}`,'#model-configuration');
  await previewTool(page);await page.getByLabel('Preview case').selectOption('fallback');
  await page.getByRole('button',{name:'Preview routing',exact:true}).click();await expect(page.locator('.routing-result h3')).toContainText('Codex');
  await shot(page,`routing-${width}`,'.routing-result');
  await page.getByLabel('Preview case').selectOption('stale');await page.getByRole('button',{name:'Preview routing',exact:true}).click();
  await expect(page.locator('.routing-result')).toContainText('Paid capacity is unknown or stale');await shot(page,`stale-${width}`,'.routing-result');
  await page.getByLabel('Preview case').selectOption('disabled');await page.getByRole('button',{name:'Preview routing',exact:true}).click();
  await expect(page.locator('.routing-result')).toContainText('Saved backend configuration is disabled');await shot(page,`disabled-${width}`,'.routing-result');
  if(width===390){
    await page.evaluate(()=>document.documentElement.style.fontSize='200%');await shot(page,'configuration-phone-200','#model-configuration');
    await page.getByRole('link',{name:'← Back to Models'}).click();await page.getByLabel('Activity view').selectOption('demo');
    await expect(page.getByText('3 distinct tasks · 6 synthetic attempts',{exact:true})).toBeVisible();await shot(page,'demo-phone-200','.attempt-chart');
    await page.getByLabel('Activity view').selectOption('observed');await shot(page,'overview-phone-200','.model-policy-summary');
  }
});

test('unknown and stale model capability stays unverified; unavailable history is recoverable',async({page})=>{
  await catalog(page,true);await page.goto('/models');
  await expect(page.getByText('Model catalog is stale; support is unverified.')).toBeVisible();
  await expect(page.getByText('Last availability evidence is stale.')).toBeVisible();
  await page.getByRole('link',{name:'Configure Codex',exact:true}).click();
  await expect(page.getByLabel('Reasoning effort · Codex').locator('option')).toHaveCount(2);
  await expect(page.getByLabel('Reasoning effort · Codex')).toHaveValue('high');
  await expect(page.getByText('Saved effort support is unverified. Choose Unspecified before saving.')).toBeVisible();
  await page.getByLabel('Reasoning effort · Codex').selectOption('');
  await manual(page,'codex','manual-unknown');
  await expect(page.getByLabel('Preferred model · Codex')).toHaveValue('manual-unknown');
  await expect(page.getByLabel('Reasoning effort · Codex').locator('option')).toHaveCount(1);
  await page.getByRole('link',{name:'← Back to Models'}).click();
  await page.route('**/api/models/activity?source=observed',route=>route.fulfill({status:503,json:{detail:'Synthetic unavailable history'}}));
  await page.reload();await expect(page.getByText('Activity history unavailable.')).toBeVisible();await expect(page.getByRole('button',{name:'Retry activity'})).toBeVisible();
  await page.unroute('**/api/models/activity?source=observed');await page.getByRole('button',{name:'Retry activity'}).click();
  await expect(page.getByText('No provider attempts recorded yet.')).toBeVisible();
});

test('reloading saved policy locks fields and failed reload preserves edits',async({page})=>{
  await page.goto('/models/configure');await manual(page,'codex','keep-this-unsaved-model');
  const model=page.getByLabel('Manual model identifier · Codex');
  let release:()=>void=()=>{};const hold=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/models',async route=>{await hold;await route.fulfill({status:503,json:{detail:'Synthetic reload failure'}});});
  await page.getByRole('button',{name:'Discard edits and reload'}).click();await expect(model).toBeDisabled();release();
  await expect(page.getByText('Reload failed. Your edits are kept.',{exact:true})).toBeVisible();await expect(model).toHaveValue('keep-this-unsaved-model');await expect(model).toBeEnabled();
  await page.unroute('**/api/models');await page.getByRole('button',{name:'Discard edits and reload'}).click();
  await expect(page.getByText('Saved preferences reloaded.',{exact:true})).toBeVisible();
  await page.getByLabel('Configure provider').selectOption('codex');await expect(page.getByLabel('Preferred model · Codex')).toHaveValue('');
});

test('specific task models and effort remain optional and use supported choices',async({page},info)=>{
 // Controlled browser transport checks the exact submitted selection. Worker and
 // persistence acceptance live in test_task_ai_settings and test_everyday.
 await page.route('**/api/local-work',r=>r.fulfill({json:{enabled:true,ready:true,bug_investigation:true,codex_configured:true,codex_available:true}}));
 await page.route('**/api/models',async r=>{
  const response=await r.fetch();const data=await response.json();
  data.policy={...data.policy,revision:4,preferred:'local',test_fallback:'both',fallback:['codex'],fallback_on:['unavailable'],substitution:'configured_fallback_only',
   providers:{...data.policy.providers,local:{enabled:true,model:'pilot-qwen38-27b',effort:null,activated:false},codex:{enabled:true,model:'fixture-codex',effort:'low',activated:false}}};
  data.capabilities.executors=[{backend:'local',models:[{model:'pilot-qwen38-27b',efforts:[]}],observed_at:new Date().toISOString(),source:'Controlled adapter',limitation:'No real provider call'},
   {backend:'codex',models:[{model:'fixture-codex',efforts:['low','high']},{model:'fixture-capable',efforts:['high']}],observed_at:new Date().toISOString(),source:'Controlled catalog',limitation:'Included access checked before use'}];
  await r.fulfill({response,json:data});
 });
 await page.goto('/intake/new?kind=issue');
 await expect(page.getByRole('button',{name:'Use my preferred AI settings'})).toBeVisible();
 await expect(page.getByLabel('Task provider',{exact:true})).toHaveCount(0);
 await page.getByLabel('Description',{exact:true}).fill('Please keep the selected guide step after reloading.');
 await page.getByRole('button',{name:'Use my preferred AI settings'}).click();
 await expect(page.getByLabel('Allow local Qwen to investigate this report')).toBeChecked();
 await page.getByLabel('Choose a different AI for this task').check();
 await page.getByLabel('Task provider',{exact:true}).selectOption('codex');
 await page.getByLabel('Task model',{exact:true}).selectOption('fixture-capable');
 await page.getByLabel('Task reasoning effort',{exact:true}).selectOption('high');
 await page.getByLabel('Allow the configured fallback before planning starts').check();
 await expect(page.getByLabel('Task fallback model',{exact:true})).toHaveValue('pilot-qwen38-27b');
 await expect(page.getByLabel('Task fallback reasoning effort')).toHaveCount(0);
 await page.reload();
 await expect(page.getByLabel('Task model',{exact:true})).toHaveValue('fixture-capable');
 await expect(page.getByLabel('Task reasoning effort',{exact:true})).toHaveValue('high');
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:1000});await overflow(page);
  await shot(page,`task-model-override-${width}`,'.task-ai-options');
 }
 await page.evaluate(()=>document.documentElement.style.fontSize='200%');await overflow(page);
 const pending=page.waitForRequest(r=>r.url().endsWith('/api/intake')&&r.method()==='POST');
 await page.route('**/api/intake',r=>r.request().method()==='POST'?r.fulfill({status:409,json:{detail:'Controlled submission captured; no provider work started.'}}):r.continue());
 await page.getByRole('button',{name:'Submit request',exact:true}).click();
 expect((await pending).postDataJSON()).toMatchObject({local_planning:'codex-with-local-fallback',ai_settings:{primary:{provider:'codex',model:'fixture-capable',effort:'high'},fallback:{provider:'local',model:'pilot-qwen38-27b',effort:null}}});
 await expect(page.getByText('Controlled submission captured; no provider work started.')).toBeVisible();
});
