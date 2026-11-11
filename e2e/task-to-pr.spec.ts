import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {artifactPath} from './output';
function fixture(action:string){execFileSync('.venv/bin/python',['scripts/fixture_ctl.py',action],{env:{...process.env,PYTHONPATH:'backend'},stdio:'pipe'});}
for(const {width,hold} of [{width:1440,hold:false},{width:390,hold:false},{width:1440,hold:true}])test(`one supported scope/budget through controlled worker and draft delivery at ${width}px${hold?' held':''}`,async({page})=>{
  fixture('reset');fixture('task-pr-setup');await page.setViewportSize({width,height:1000});
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Supported task').selectOption({label:'NON-MODEL supported task'});
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('task-pr-step');await page.reload();
  await page.getByText('Optional permission for this ready-made task to run and open a draft pull request',{exact:true}).click();
  const option=page.getByLabel('Also allow this task to run and open one draft pull request');
  await expect(option).toBeVisible();await option.focus();await page.keyboard.press('Space');
  await expect(option).toBeChecked();
  await page.screenshot({path:artifactPath(`task-to-pr/budget-${width}.png`),fullPage:false});
  await page.getByRole('button',{name:'Approve plan and allow this run',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  fixture('task-pr-step');fixture('task-pr-step');await page.reload();
  fixture('task-pr-step');fixture('task-pr-step');await page.reload();
  await page.getByRole('link',{name:'Review changes and checks',exact:true}).click();
  await expect(page.getByRole('heading',{name:/(Fix prepared|Result checked) — automated checks passed/,exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Preparing draft PR delivery'})).toBeVisible();
  await page.screenshot({path:artifactPath(`task-to-pr/waiting-${width}.png`),fullPage:false});
  if(hold){fixture('task-pr-hold');await page.reload();await expect(page.getByRole('heading',{name:'PR delivery needs attention'})).toBeVisible();await expect(page.getByText('The target base moved.',{exact:false})).toBeVisible();await page.screenshot({path:artifactPath('task-to-pr/held-desktop.png')});return;}
  fixture('task-pr-finish');await page.reload();
  await expect(page.getByText('No real PR was created.',{exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:'Review the draft PR'})).toHaveCount(0);
  await page.getByText('PR summary and destination',{exact:true}).focus();await page.keyboard.press('Enter');
  await expect(page.getByText('Twich-Team/TwichUI · main · Feature',{exact:true})).toBeVisible();
  if(width===390)await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:artifactPath(`task-to-pr/delivered-${width}.png`),fullPage:false});
  fixture('task-pr-finish');await page.reload();
  await expect(page.getByRole('heading',{name:'Controlled delivery result'})).toBeVisible();
  if(width===1440){fixture('admission-stale');await page.reload();await expect(page.getByText('Historical delivery evidence.',{exact:false})).toBeVisible();await page.screenshot({path:artifactPath('task-to-pr/history-desktop.png')});}
});
