import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {artifactPath} from './output';
function fixture(action:string){execFileSync('.venv/bin/python',['scripts/fixture_ctl.py',action],{env:{...process.env,PYTHONPATH:'backend'},stdio:'pipe'});}
for(const width of [1440,390])test(`NON-MODEL site session preparation, stop and recovery at ${width}`,async({page})=>{
  fixture('reset');fixture('task-pr-setup');await page.setViewportSize({width,height:1000});
  await page.goto('/intake/new?kind=feature');await expect(page.getByText('Local Workbench · Task-specific permissions',{exact:true})).toBeVisible();await expect(page.getByText('Local examples or read-only observations · Nothing is published here',{exact:true})).toHaveCount(0);await page.getByLabel('Supported task').selectOption({label:'NON-MODEL supported task'});
  await page.getByLabel('Does this request change the interface?').selectOption('nonvisual');await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();fixture('task-pr-step');await page.reload();
  await page.getByLabel('Approve this scope and one task-to-PR budget together').check();await page.getByRole('button',{name:'Approve scope and one task-to-PR run',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();fixture('task-pr-step');fixture('task-pr-step');
  let stage='preparing',stopped=false;
  await page.route('**/api/intake/*/tasks/*/execution',async route=>{
    const response=await route.fetch();const data=await response.json();
    await route.fulfill({json:{...data,runs:[],stage,message:stage==='held'?'NON-MODEL readiness failed. No model started.':stopped?'NON-MODEL stopping; waiting for restoration.':'NON-MODEL preparing isolated verification.',execution_configured:true,session:{id:'a4abbc91-7d5f-4a98-a253-36054e4c90c6',status:stage==='held'?'held':'active',message:'NON-MODEL controlled session',can_stop:stage!=='held'&&!stopped,cancel_requested:stopped}}});
  });
  await page.route('**/api/task-sessions/*/stop',async route=>{expect(route.request().method()).toBe('POST');expect(JSON.parse(route.request().postData()!).command_id).toMatch(/^[a-f0-9-]{36}$/);stopped=true;await route.fulfill({json:{status:'cancel_requested'}});});
  await page.reload();await expect(page.getByRole('heading',{name:'Preparing approved task'})).toBeVisible();
  const stop=page.getByRole('button',{name:'Stop this run',exact:true});await stop.focus();await page.keyboard.press('Enter');
  await expect(page.getByText('NON-MODEL stopping; waiting for restoration.',{exact:true})).toBeVisible();
  stage='recovering';await page.reload();await expect(page.getByRole('heading',{name:'Waiting for safe recovery'})).toBeVisible();
  if(width===390)await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.getByRole('heading',{name:'Waiting for safe recovery'}).scrollIntoViewIfNeeded();await page.screenshot({path:artifactPath(`site-session/recovery-${width}.png`)});
  stage='held';await page.reload();await expect(page.getByText('NON-MODEL readiness failed. No model started.',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Stop this run',exact:true})).toHaveCount(0);
  await page.getByText('NON-MODEL readiness failed. No model started.',{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:artifactPath(`site-session/held-${width}.png`)});
});
