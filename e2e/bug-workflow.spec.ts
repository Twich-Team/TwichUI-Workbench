import {test,expect} from '@playwright/test';
import {fixture,overflow,capture} from './helpers';

test('ordinary bug report reaches a checked local patch through separate browser approvals',async({page},info)=>{
  test.skip(process.env.WORKBENCH_E2E_BUG!=='1','Uses the disposable everyday app with controlled providers.');
  test.setTimeout(60000);
  fixture('reset');
  fixture('reviewed-offer-bug-setup');
  await page.goto('/models/configure?provider=local');
  await page.getByLabel('Preferred model · Local LLM').selectOption('pilot-qwen38-27b');
  await page.getByRole('button',{name:'Save model preferences'}).click();
  await expect(page.getByText('Preferences saved for eligible new requests. Each task still needs approval.')).toBeVisible();

  await page.goto('/intake/new?kind=issue');
  await page.getByLabel('Title Optional').fill('Name normalization leaves uppercase letters');
  await page.getByLabel('Description', {exact:true}).fill('Entering ALICE with surrounding spaces returns ALICE. It should return alice.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByLabel('Allow local Qwen to investigate this report').check();
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('reviewed-offer-bug-tick');
  await page.reload();
  const plan=page.getByRole('region',{name:'Proposed repair check'});
  await expect(plan.getByText('Incorrect result to catch')).toBeVisible();
  const approve=page.getByRole('button',{name:'Approve plan',exact:true});
  await expect(approve).toBeDisabled();
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    await plan.scrollIntoViewIfNeeded();
    await overflow(page);
    await capture(page,info,`bug-plan-${width}`,{keepInCompact:true});
  }
  await page.getByLabel('The reproduction and expected behavior match my report.').check();
  await approve.click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  for(let i=0;i<3;i++)fixture('reviewed-offer-bug-tick');

  const offer=page.getByRole('region',{name:'Certified task run approval'});
  await expect(offer.getByRole('heading',{name:'Run the approved work in a separate copy'})).toBeVisible();
  const run=offer.getByRole('button',{name:'Approve local run',exact:true});
  await expect(run).toBeDisabled();
  await expect(offer.getByText('Publishing options')).toHaveCount(0);
  await offer.getByRole('checkbox',{name:'I reviewed what this run allows.'}).check();
  await run.click();
  await expect(page.getByRole('heading',{name:'Task budget saved · Waiting to apply'})).toBeVisible();
  fixture('reviewed-offer-bug-tick');
  fixture('reviewed-offer-bug-tick');
  fixture('reviewed-offer-bug-run');

  await page.getByRole('link',{name:'Review changes and checks'}).click();
  const result=page.getByRole('region',{name:'Run result and checks'});
  await expect(result.getByRole('heading',{name:'Changes checked'})).toBeVisible();
  await expect(result.getByText(/reproduced the bug in the original/)).toBeVisible();
  await result.getByText('See the changes in code').click();
  await expect(result.getByText(/:lower\(\)/)).toBeVisible();
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    await result.scrollIntoViewIfNeeded();
    await overflow(page);
    await capture(page,info,`bug-result-${width}`,{keepInCompact:true});
  }
  await page.reload();
  await expect(result.getByRole('heading',{name:'Changes checked'})).toBeVisible();
  const history=await page.request.get('/api/real-executions').then(r=>r.json());
  expect(history.total).toBe(1);
});
