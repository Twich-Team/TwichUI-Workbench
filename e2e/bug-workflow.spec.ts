import {test,expect} from '@playwright/test';
import {fixture,overflow,capture} from './helpers';

test('read-only refinement and scope choice reach a fresh review and separate run permission',async({page},info)=>{
  test.skip(process.env.WORKBENCH_E2E_BUG!=='1','Uses the disposable everyday app with controlled providers.');
  test.setTimeout(60000);
  fixture('reset');fixture('reviewed-offer-bug-scope-setup');
  await page.goto('/models/configure?provider=local');
  await page.getByLabel('Preferred model · Local LLM').selectOption('pilot-qwen38-27b');
  await page.getByRole('button',{name:'Save model preferences'}).click();
  await expect(page.getByText('Preferences saved for eligible new requests. Each task still needs approval.')).toBeVisible();
  await page.goto('/intake/new?kind=issue');
  await page.getByLabel('Title Optional').fill('Name normalization leaves uppercase letters');
  await page.getByLabel('Description',{exact:true}).fill('Entering ALICE with surrounding spaces returns ALICE. It should return alice.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('unclassified');
  await page.getByRole('button',{name:'Use my preferred AI settings',exact:true}).click();
  await expect(page.getByLabel('Allow local Qwen to investigate this report')).toBeChecked();
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('reviewed-offer-bug-scope-tick');
  await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
  await page.getByText('Ask Workbench to refine this plan',{exact:true}).click();
  await page.getByLabel('What should the plan explain or reconsider?').fill('Make the remaining uncertainty and bounded repair scope clearer.');
  await page.getByRole('button',{name:'Refine this plan',exact:true}).click();
  fixture('reviewed-offer-bug-scope-tick');await page.reload();
  await page.getByText('Change what this plan may cover',{exact:true}).click();
  await page.getByLabel('Does this change how the addon looks?',{exact:true}).selectOption('nonvisual');
  await page.getByLabel('Why are you changing the scope?').fill('Correct the behavior while preserving the existing layout and unrelated data.');
  await page.getByRole('button',{name:'Save and review the updated plan'}).click();
  await page.reload();
  await expect(page.getByRole('heading',{name:'The plan',exact:true})).toBeVisible();
  const id=page.url().split('/intake/')[1].split('?')[0];
  const saved=await page.request.get('/api/intake/'+id).then(r=>r.json());
  expect(saved.revision).toBe(3);expect(saved.status).toBe('completed');
  expect(saved.proposals[0].approval_blockers).toEqual([]);
  expect(saved.provider_activity).toHaveLength(2);expect(saved.decisions).toHaveLength(0);
  expect(saved.jobs.at(-1).operation_kind).toBe('scope_binding');
  await page.getByLabel('The repair scope and behavior criteria match my report.').check();
  await page.getByRole('button',{name:'Approve plan',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  for(let i=0;i<3;i++)fixture('reviewed-offer-bug-scope-tick');
  const offer=page.getByRole('region',{name:'Scoped repair run approval'});
  await expect(offer.getByRole('heading',{name:'Run the approved work in a separate copy'})).toBeVisible();
  await expect(offer.getByRole('button',{name:'Approve local run',exact:true})).toBeDisabled();
  // This test deliberately does not approve implementation. Plan review must
  // reach a distinct, unapproved offer without another generation/checker gate.
  const history=await page.request.get('/api/real-executions').then(r=>r.json());expect(history.total).toBe(0);
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});await overflow(page);
    await capture(page,info,`scope-review-to-offer-${width}`,{keepInCompact:true});
  }
});

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
  await page.getByText('Provider permissions and investigation limits',{exact:true}).click();
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
  await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();
  fixture('reviewed-offer-bug-tick');
  fixture('reviewed-offer-bug-tick');
  fixture('reviewed-offer-bug-run');

  await page.getByRole('link',{name:'Review changes and checks'}).click();
  const result=page.getByRole('region',{name:'Run result and checks'});
  await expect(result.getByRole('heading',{name:/(Fix prepared|Result checked) — automated checks passed/})).toBeVisible();
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
  await expect(result.getByRole('heading',{name:/(Fix prepared|Result checked) — automated checks passed/})).toBeVisible();
  const history=await page.request.get('/api/real-executions').then(r=>r.json());
  expect(history.total).toBe(1);
});
