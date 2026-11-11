import {test,expect} from '@playwright/test';
import {fixture,overflow,capture} from './helpers';

test.beforeEach(()=>fixture('reset'));

test('ordinary regression request prepares a bound contract without a registered recipe or run',async({page},info)=>{
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Optional').fill('Regression coverage for invalid profile names');
  await page.getByLabel('Description', {exact:true}).fill('In tests/test_profiles.lua cover rejecting invalid names while preserving the selected profile.');
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('Invalid names are rejected.\nThe selected profile stays unchanged.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('regression-planning');
  const contract=page.getByRole('region',{name:'Proposed test'});
  await expect(contract.getByRole('heading',{name:'What the test would check'})).toBeVisible();
  await expect(contract.getByText('tests/test_profiles.lua',{exact:true})).toBeVisible();
  await expect(contract.getByText('Proposed verification · Not run')).not.toBeVisible();
  await contract.getByText('Technical test plan and checks',{exact:true}).click();
  await expect(contract.getByText('Proposed verification · Not run')).toBeVisible();
  await expect(contract.getByText(/Before execution, independently certify/).first()).toBeVisible();
  await expect(page.getByRole('checkbox',{name:'Also allow this task to run and open one draft pull request'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeEnabled();
  const identity=page.url().split('/intake/')[1].split('?')[0];
  const saved=await page.request.get('/api/intake/'+identity).then(r=>r.json());
  expect(saved.proposals[0].basis).toBe('preparation');
  expect(saved.proposals[0].task_contract.execution_authorized).toBe(false);
  expect(saved.proposals[0].task_contract.resolved_base).toMatch(/^[a-f0-9]{40}$/);
  expect(saved.decisions).toEqual([]);
  expect(saved.tasks).toEqual([]);
  await overflow(page);await capture(page,info,'regression-contract-desktop',{keepInCompact:true});
  await page.setViewportSize({width:390,height:844});
  await overflow(page);await capture(page,info,'regression-contract-phone',{keepInCompact:true});
  await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  await overflow(page);await capture(page,info,'regression-contract-phone-enlarged',{keepInCompact:true});
  await page.evaluate(()=>document.documentElement.style.fontSize='');
  await page.setViewportSize({width:320,height:740});await overflow(page);
  await page.reload();
  await expect(contract.getByRole('heading',{name:'What the test would check'})).toBeVisible();
  const again=await page.request.get('/api/intake/'+identity).then(r=>r.json());
  expect(again.proposals[0]).toEqual(saved.proposals[0]);
});

test('missing target is explained and clarified without granting permissions',async({page},info)=>{
  await page.setViewportSize({width:390,height:844});
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Optional').fill('Regression coverage for truncated sharing payloads');
  await page.getByLabel('Description', {exact:true}).fill('Exercise rejection of a truncated settings payload.');
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('A truncated payload does not apply any settings.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('regression-planning');
  await expect(page.getByRole('heading',{name:'Workbench needs your help'})).toBeVisible();
  await expect(page.getByText('No test file selected.')).not.toBeVisible();
  await page.getByText('Read the supporting plan',{exact:true}).click();
  await expect(page.getByText('No test file selected.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toHaveCount(0);
  await overflow(page);await capture(page,info,'regression-missing-phone');
  await page.getByLabel('Possible existing test').selectOption('tests/test_share.lua');
  await page.getByRole('button',{name:'Use this test'}).click();
  fixture('regression-planning');
  await page.getByRole('button',{name:'Open current proposal'}).click();
  const testPlan=page.getByRole('region',{name:'Proposed test'});
  await expect(testPlan.getByText('tests/test_share.lua',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Workbench needs your help'})).toHaveCount(0);
  await overflow(page);
});

for(const width of [1440,390])test(`generated behavioral examples need independent review before a certified offer at ${width}`,async({page})=>{
  fixture('reviewed-offer-setup');await page.setViewportSize({width,height:1000});
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Optional').fill('CONTROLLED regression request with generated behavioral checks');
  await page.getByLabel('Description', {exact:true}).fill('In tests/test_normalize.lua cover trimming surrounding spaces and lowercasing names.');
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('Names with surrounding spaces and capitals normalize to lowercase.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('reviewed-offer-scenario-plan');await page.reload();
  const contract=page.getByRole('region',{name:'Proposed test'});
  await expect(contract.getByText('Examples to review')).toBeVisible();
  const approve=page.getByRole('button',{name:'Approve plan',exact:true});
  await expect(approve).toBeDisabled();
  await expect(contract.getByText('"alice"',{exact:true})).toBeVisible();
  if(width===390)await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  await overflow(page);
  await contract.getByText('Examples to review').scrollIntoViewIfNeeded();
  await page.screenshot({path:(await import('./output')).artifactPath(`generated-expectations/expectations-viewport-${width}.png`)});
  await contract.getByText('"alice"',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:(await import('./output')).artifactPath(`generated-expectations/observation-viewport-${width}.png`)});
  await page.getByLabel('The test examples match what I asked for.').scrollIntoViewIfNeeded();
  await page.screenshot({path:(await import('./output')).artifactPath(`generated-expectations/confirmation-viewport-${width}.png`)});
  if(width===390)await page.evaluate(()=>document.documentElement.style.fontSize='');
  await page.getByLabel('The test examples match what I asked for.').check();
  await expect(approve).toBeEnabled();
  const submission=page.waitForResponse(r=>r.url().endsWith('/decisions')&&r.request().method()==='POST');
  await approve.click();expect((await submission).status()).toBe(200);
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  fixture('reviewed-offer-scope');fixture('reviewed-offer-scenario-certify');await page.reload();
  const offer=page.getByRole('region',{name:'Certified task run approval'});
  await expect(offer.getByRole('heading',{name:'Add test coverage in a separate copy'})).toBeVisible();
  await offer.screenshot({path:(await import('./output')).artifactPath(`generated-expectations/offer-${width}.png`)});
  await offer.getByText('Publishing options',{exact:true}).click();
  await offer.getByRole('radio',{name:'Also allow one draft pull request'}).check();
  await offer.getByRole('checkbox',{name:'I reviewed the run and publishing permission.'}).check();
  await offer.getByRole('button',{name:'Approve run and draft pull request',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();
  const result=JSON.parse(fixture('reviewed-offer-controller'));
  expect(result.controller_calls).toBe(1);expect(result.preparations[0].certified).toBe(true);
  expect(result.authority_counts).toMatchObject({real_execution_grants:0,real_execution_requests:0,model_attempts:0,real_publications:0,task_sessions:1});
  const identity=page.url().split('/intake/')[1].split('?')[0];
  const current=await page.request.get('/api/intake/'+identity).then(r=>r.json());
  expect(current.decisions.find((d:{payload:{expectation_review?:string}})=>d.payload.expectation_review).payload.expectation_review).toMatch(/^[a-f0-9]{64}$/);
  const {writeFileSync}=await import('node:fs');const {artifactPath}=await import('./output');
  writeFileSync(artifactPath(`generated-expectations/handoff-${width}.json`),JSON.stringify(result,null,2));
});

test('generated expectation mismatch stays a specific browser hold after review',async({page})=>{
  fixture('reviewed-offer-setup');await page.setViewportSize({width:390,height:1000});
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Optional').fill('CONTROLLED wrong expectation must hold');
  await page.getByLabel('Description', {exact:true}).fill('In tests/test_normalize.lua cover trimming spaces and lowercasing names.');
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('Names become lowercase.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('reviewed-offer-scenario-wrong-plan');await page.reload();
  // Even an incorrect human confirmation cannot turn mismatching evidence into certification.
  await page.getByLabel('The test examples match what I asked for.').check();
  await page.getByRole('button',{name:'Approve plan',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  fixture('reviewed-offer-scope');
  const result=JSON.parse(fixture('reviewed-offer-scenario-certify-hold'));
  expect(result.status).toBe('hold');expect(result.message).toContain('capital-name');
  await page.reload();
  const hold=page.getByRole('region',{name:'Task preparation hold'});
  await expect(hold.getByText(/capital-name: the expected observation or fixture setup/)).toBeVisible();
  await expect(page.getByRole('region',{name:'Certified task run approval'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Approve run and draft pull request',exact:true})).toHaveCount(0);
  await overflow(page);await hold.screenshot({path:(await import('./output')).artifactPath('generated-expectations/specific-expectation-hold-phone.png')});
});

const moduleExample = 'Example 1: {"args":["  ALICE "],"expected":"alice","incorrect":"ALICE"}';
const clientExample = 'Example 1: {"args":["  bob  "],"expected":"bob","incorrect":"  bob  "}';

for(const width of [1440,390])test(`application worker prepares grounded ordinary requests after scope review at ${width}`,async({page})=>{
  fixture('reviewed-offer-automatic-setup');
  await page.setViewportSize({width,height:1000});
  const client=width===390;
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Optional').fill(client?'Cover storing a trimmed last name':'Cover lowercase name normalization');
  await page.getByLabel('Description', {exact:true}).fill(client
    ? `In tests/test_client.lua cover trimming spaces before storing the last name.\n${clientExample}`
    : `In tests/test_normalize.lua cover trimming spaces and lowercasing names.\n${moduleExample}`);
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill(client?'The last name stores bob after a spaced input.':'Spaces and capital letters normalize to alice.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  const planned=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(planned.adapter_calls).toBe(1);await page.reload();
  const contract=page.getByRole('region',{name:'Proposed test'});
  await expect(contract.getByText('Examples to review')).toBeVisible();
  await expect(contract.getByText(client?'"bob"':'"alice"',{exact:true})).toBeVisible();
  const approve=page.getByRole('button',{name:'Approve plan',exact:true});
  await expect(approve).toBeDisabled();
  if(client)await page.evaluate(()=>document.documentElement.style.fontSize='200%');
  await contract.getByText('Expected result 1',{exact:false}).scrollIntoViewIfNeeded();
  await overflow(page);
  const {artifactPath}=await import('./output');
  await page.screenshot({path:artifactPath(`automatic-scenarios/review-${width}.png`)});
  await contract.getByText(client?'"bob"':'"alice"',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:artifactPath(`automatic-scenarios/observation-${width}.png`)});
  const confirmation=page.getByLabel('The test examples match what I asked for.');
  await confirmation.check();await approve.click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  if(client)await page.evaluate(()=>document.documentElement.style.fontSize='');
  const prepared=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(prepared.adapter_calls).toBe(0);
  expect(prepared.jobs.find((j:{kind:string})=>j.kind==='behavioral_preparation').status).toBe('completed');
  expect(prepared.grants).toBe(0);expect(prepared.requests).toBe(0);expect(prepared.publications).toBe(0);
  expect(prepared.model_attempts).toEqual([{source:'demo',outcome:'completed',model:'controlled-scenario-adapter'}]);
  await page.reload();
  const offer=page.getByRole('region',{name:'Certified task run approval'});
  await expect(offer.getByRole('heading',{name:'Add test coverage in a separate copy'})).toBeVisible();
  await offer.screenshot({path:artifactPath(`automatic-scenarios/offer-${width}.png`)});
  await page.getByText('Original request, planning history and evidence',{exact:true}).click();
  await page.getByText('Behavioral scenario preparation · Revision 1',{exact:true}).scrollIntoViewIfNeeded();
  await expect(page.getByText('Behavioral scenario preparation · Revision 1')).toBeVisible();
  await overflow(page);await page.screenshot({path:artifactPath(`automatic-scenarios/preparation-${width}.png`)});
  const replay=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(replay.adapter_calls).toBe(0);expect(replay.jobs).toEqual(prepared.jobs);
  const {writeFileSync}=await import('node:fs');
  writeFileSync(artifactPath(`automatic-scenarios/worker-${width}.json`),JSON.stringify({planned,prepared,replay},null,2));
});

test('grounded intake asks for exact example values before opening approval',async({page})=>{
  fixture('reviewed-offer-automatic-setup');
  await page.goto('/');
  await page.getByRole('link',{name:'Create a task',exact:true}).filter({visible:true}).click();
  await page.getByLabel('Type of work').selectOption('coverage');
  await page.getByLabel('Title Optional').fill('Cover lowercase name normalization');
  await page.getByLabel('Description', {exact:true}).fill('Synthetic fixture only. In tests/test_normalize.lua cover trimming spaces and lowercasing names.');
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('Spaces and capital letters normalize to alice.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  const held=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(held).toMatchObject({adapter_calls:0,model_attempts:[],grants:0,requests:0,publications:0});
  await page.reload();
  await expect(page.getByText('Waiting for information',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toHaveCount(0);
  await expect(page.getByText('Expected result 1',{exact:true})).toHaveCount(0);
  const question=page.getByRole('heading',{name:'Workbench needs your help'});
  await expect(page.getByText(/what exact arguments/).first()).not.toBeVisible();
  await page.getByRole('textbox',{name:'Input 1',exact:true}).fill('  ALICE ');
  await page.getByRole('textbox',{name:'Expected result',exact:true}).fill('alice');
  await page.getByRole('textbox',{name:'Wrong result to catch',exact:true}).fill('ALICE');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    await question.scrollIntoViewIfNeeded();
    await overflow(page);
    await page.screenshot({path:(await import('./output')).artifactPath(`screenshots/grounded-clarification-${width}.png`)});
  }
  const answered=page.waitForResponse(r=>r.url().endsWith('/clarification')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Send example'}).click();
  const answer=await answered;
  expect(answer.status()).toBe(200);
  expect(JSON.parse(answer.request().postDataJSON().text.split('Example 1: ')[1])).toMatchObject({args:['  ALICE '],expected:'alice',incorrect:'ALICE'});
  const planned=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(planned.adapter_calls).toBe(1);
  await page.reload();
  await expect(page.getByLabel('The test examples match what I asked for.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Approve plan',exact:true})).toBeDisabled();
});

test('application preparation policy denial is a specific hold without a duplicate retry button',async({page})=>{
  fixture('reviewed-offer-automatic-setup');await page.setViewportSize({width:390,height:1000});
  await page.goto('/intake/new?kind=feature');
  await page.getByLabel('Title Optional').fill('Cover lowercase normalization with reviewed policy');
  await page.getByLabel('Description', {exact:true}).fill(`In tests/test_normalize.lua cover trimming spaces and lowercasing names.\n${moduleExample}`);
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('Spaces and capital letters normalize to alice.');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  fixture('reviewed-offer-automatic-worker');await page.reload();
  await page.getByLabel('The test examples match what I asked for.').check();
  await page.getByRole('button',{name:'Approve plan',exact:true}).click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  const result=JSON.parse(fixture('reviewed-offer-automatic-denied-worker'));
  expect(result.jobs.find((j:{kind:string})=>j.kind==='behavioral_preparation').status).toBe('failed');
  expect(result.grants).toBe(0);expect(result.requests).toBe(0);expect(result.publications).toBe(0);
  await page.reload();
  const hold=page.getByRole('region',{name:'Task preparation hold'});
  await expect(hold.getByText('Preparation held: Preparation policy denies this task class')).toBeVisible();
  await expect(page.getByRole('button',{name:'Retry planning',exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Certified task run approval'})).toHaveCount(0);
  await overflow(page);
  const {artifactPath}=await import('./output');
  await hold.screenshot({path:artifactPath('automatic-scenarios/policy-hold-390.png')});
});

async function openLocalOffer(page: import('@playwright/test').Page) {
  fixture('reviewed-offer-automatic-setup');
  await page.goto('/');
  await page.getByRole('link',{name:'Create a task',exact:true}).filter({visible:true}).click();
  await page.getByLabel('Type of work').selectOption('coverage');
  await page.getByLabel('Title Optional').fill('Cover lowercase name normalization');
  await page.getByLabel('Description', {exact:true}).fill('Cover trimming spaces and lowercasing names.');
  await page.getByLabel('Add a check for existing behavior').check();
  await page.getByLabel('Expected test results').fill('Spaces and capital letters normalize to alice.');
  await page.getByText('Optional test details',{exact:true}).click();
  await page.getByLabel('Existing test paths').fill('tests/test_normalize.lua');
  await page.getByLabel('Add a text example for the first expected result').check();
  await page.getByLabel('Example input',{exact:true}).fill('  ALICE ');
  await page.getByLabel('Expected example result',{exact:true}).fill('alice');
  await page.getByLabel('Incorrect example result to reject',{exact:true}).fill('ALICE');
  await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
  await page.getByRole('button',{name:'Submit request',exact:true}).click();
  await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
  const planned=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(planned.adapter_calls).toBe(1);
  const proposal=page.getByRole('region',{name:'Proposed test'});
  await expect(proposal.getByText('"alice"',{exact:true})).toBeVisible();
  await expect(proposal.getByText('"ALICE"',{exact:true})).toBeVisible();
  const approve=page.getByRole('button',{name:'Approve plan',exact:true});
  await expect(approve).toBeDisabled();
  await page.getByLabel('The test examples match what I asked for.').check();
  await approve.click();
  await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
  fixture('reviewed-offer-automatic-tick');
  fixture('reviewed-offer-automatic-tick');
  const preparation=page.getByRole('region',{name:'Task preparation hold'});
  await expect(preparation.getByRole('heading',{name:'Preparing the approved work'})).toBeVisible();
  await expect(preparation.getByRole('status')).toContainText('No repair has started.');
  const submitted=await page.request.get('/api'+new URL(page.url()).pathname).then(r=>r.json());
  expect(submitted.original.description).toBe('Cover trimming spaces and lowercasing names.');
  expect(submitted.original.regression_tests.text_example).toEqual({input:'  ALICE ',expected:'alice',incorrect:'ALICE'});
  const prepared=JSON.parse(fixture('reviewed-offer-automatic-worker'));
  expect(prepared).toMatchObject({adapter_calls:0,grants:0,requests:0,publications:0});
  const offer=page.getByRole('region',{name:'Certified task run approval'});
  await expect(offer.getByRole('radio',{name:'Keep the result local',includeHidden:true})).toBeChecked();
  await expect(offer.getByText(/Result stays local/)).toBeVisible();
  await expect(offer.getByRole('button',{name:'Approve local run',exact:true})).toBeDisabled();
  await expect(offer.getByText('tests/test_normalize.lua',{exact:true})).toBeVisible();
  return offer;
}

for(const outcome of ['pass','fail','interrupt'] as const)test(`browser local-only approval reaches the worker and retains ${outcome}`,async({page})=>{
  test.setTimeout(45000);
  const offer=await openLocalOffer(page);
  const button=offer.getByRole('button',{name:'Approve local run',exact:true});
  const confirm=offer.getByRole('checkbox',{name:'I reviewed what this run allows.'});
  await confirm.check();
  await offer.getByText('Publishing options',{exact:true}).click();
  await offer.getByRole('radio',{name:'Also allow one draft pull request'}).check();
  await expect(offer.getByRole('button',{name:'Approve run and draft pull request'})).toBeDisabled();
  await offer.getByRole('radio',{name:'Keep the result local'}).check();
  await expect(button).toBeDisabled();
  await confirm.check();
  if(outcome==='pass'){
    const {artifactPath}=await import('./output');
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await overflow(page);
      await offer.screenshot({path:artifactPath(`local-browser/approval-${width}.png`)});
    }
  }
  const sent=page.waitForResponse(r=>r.url().endsWith('/run-approval')&&r.request().method()==='POST');
  await button.dblclick();
  const first=await sent;
  expect(first.status()).toBe(200);
  const body=first.request().postDataJSON();
  expect(body.run_budget).toMatchObject({publication:'hold',activate_once:true,fallback:false});
  await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();

  // Exercise server-side replay and attempted publication escalation after the browser decision.
  const csrf=await page.request.get('/api/session').then(r=>r.json());
  const headers={'Origin':new URL(page.url()).origin,'X-CSRF-Token':csrf.csrf_token};
  const same=await page.request.post(first.url(),{headers,data:body});
  expect(same.status()).toBe(200);
  expect((await same.json()).id).toBe(body.command_id);
  const duplicate=await page.request.post(first.url(),{headers,data:{...body,
    command_id:crypto.randomUUID(),run_budget:{...body.run_budget,publication:'draft-pr'}}});
  expect(duplicate.status()).toBe(409);
  expect(JSON.stringify(await duplicate.json())).toContain('already saved');

  fixture('reviewed-offer-automatic-local-wait');
  await expect(page.getByRole('heading',{name:'Preparing approved task'})).toBeVisible();
  await page.getByText('Permission for this run',{exact:true}).click();
  await expect(page.getByRole('heading',{name:'Run approved'})).toBeVisible();
  await expect(page.getByText('The result stays in Workbench. Publication requires separate approval.')).toBeVisible();
  const result=JSON.parse(fixture('reviewed-offer-automatic-local-'+outcome));
  expect(result).toMatchObject({adapter_calls:0,grants:1,requests:1,publications:0});
  if(outcome==='interrupt'){
    await expect(page.getByRole('heading',{name:'Waiting for safe recovery'})).toBeVisible();
    fixture('reviewed-offer-automatic-local-recover');
    await expect(page.getByRole('heading',{name:'Interrupted · Evidence retained'})).toBeVisible();
  }
  const progress=page.getByRole('region',{name:'Task implementation progress'});
  await progress.getByRole('link',{name:outcome==='pass'?'Review changes and checks':'View run details'}).click();
  const review=page.getByRole('region',{name:'Run result and checks'});
  await expect(review).toBeVisible();
  const executionPath='/api/real-executions/'+new URL(page.url()).pathname.split('/').at(-1);
  const execution=await page.request.get(executionPath).then(r=>r.json());
  expect(execution.status).toBe({pass:'checked',fail:'failed',interrupt:'interrupted'}[outcome]);
  expect(execution.publication_requested).toBe(false);
  expect(await page.request.get(executionPath+'/delivery').then(r=>r.json())).toBeNull();
  if(outcome==='pass'){
    await expect(review.getByRole('heading',{name:'Test coverage added — automated checks passed'})).toBeVisible();
    await review.getByText('See the changes in code',{exact:true}).click();
    await review.getByText('Check details',{exact:true}).click();
    await expect(review.getByLabel('Unified diff for tests/test_normalize.lua')).toContainText('assert(normalize("  ALICE ") == "alice")');
    await expect(review.locator('.check-summary').getByText('Passed',{exact:true})).toHaveCount(4);
    await expect(review.getByText('Technical results and run details',{exact:true})).toBeVisible();
    await review.getByText('Technical results and run details',{exact:true}).click();
    await review.getByText('Check 4 · pass · exit 0',{exact:true}).click();
    await expect(review.getByText(/5 passed, 0 failed/)).toBeVisible();
    await review.getByText('Technical results and run details',{exact:true}).click();
    await review.getByText('See the changes in code',{exact:true}).click();
    const {artifactPath}=await import('./output');
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await overflow(page);
      await review.screenshot({path:artifactPath(`local-browser/result-${width}.png`)});
    }
    await page.goto('/');
    const recent=page.locator('.workshop-panel').filter({has:page.getByRole('heading',{name:'Recent outcomes',exact:true})});
    await expect(recent.getByRole('link',{name:/Cover lowercase name normalization.*Ready to review/})).toBeVisible();
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await overflow(page);
      await page.screenshot({path:artifactPath(`local-browser/work-${width}.png`),fullPage:true});
    }
  }else if(outcome==='fail'){
    await expect(review.getByRole('heading',{name:'Work could not finish'})).toBeVisible();
    const failure=review.getByRole('region',{name:'Last failed check'});
    await failure.getByText('See the failure output',{exact:true}).click();
    await expect(failure.getByText(/assertion failed/)).toBeVisible();
    await expect(review.getByText('No changes were saved for review.')).toBeVisible();
  }else{
    await expect(review.getByRole('heading',{name:'Interrupted · Evidence retained'})).toBeVisible();
    await expect(review.getByText('Workbench could not confirm how the model call ended. This attempt has been used.')).toBeVisible();
  }
});

test('browser approval refuses changed certified evidence before saving a run',async({page})=>{
  test.setTimeout(45000);
  const offer=await openLocalOffer(page);
  await offer.getByRole('checkbox',{name:'I reviewed what this run allows.'}).check();
  await page.route('**/run-approval',async route=>{
    if(route.request().method()==='POST')fixture('reviewed-offer-stale');
    await route.continue();
  });
  const sent=page.waitForResponse(r=>r.url().endsWith('/run-approval')&&r.request().method()==='POST');
  await offer.getByRole('button',{name:'Approve local run',exact:true}).click();
  expect((await sent).status()).toBe(409);
  await expect(offer.getByRole('alert').filter({hasText:'offer changed'}).first()).toBeVisible();
  await offer.getByRole('button',{name:'Review current offer'}).click();
  const hold=page.getByRole('region',{name:'Task preparation hold'});
  await expect(hold.getByRole('heading',{name:'Preparation is on hold'})).toBeVisible();
  expect(await page.request.get('/api'+new URL(page.url()).pathname).then(r=>r.json()).then(r=>r.decisions)).toHaveLength(1);
  expect(await page.request.get('/api/real-executions').then(r=>r.json()).then(r=>r.total)).toBe(0);
});

test('expired local approval holds the task without starting a run', async ({page})=>{
  test.setTimeout(45000);
  const offer=await openLocalOffer(page);
  const task=new URL(page.url()).pathname;
  await offer.getByRole('checkbox',{name:'I reviewed what this run allows.'}).check();
  await offer.getByRole('button',{name:'Approve local run',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();
  const expired=JSON.parse(fixture('reviewed-offer-automatic-local-expire'));
  expect(expired).toMatchObject({grants:0,requests:0,publications:0});
  await page.reload();
  await expect(page.getByRole('region',{name:'Task implementation progress'}).getByRole('heading',{name:'Run approval expired',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Approve local run',exact:true})).toHaveCount(0);
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Needs your attention'})).toBeVisible();
  await expect(page.locator('.workshop-panel').filter({has:page.getByRole('heading',{name:'Needs your attention',exact:true})}).locator(`a[href="${task}"]`)).toBeVisible();
});

test('everyday intake completes two different local tasks in one saved history',async({page})=>{
  test.skip(process.env.WORKBENCH_E2E_EVERYDAY!=='1','Uses the dedicated disposable everyday fixture.');
  test.setTimeout(60000);
  fixture('reviewed-offer-automatic-setup');
  const {artifactPath}=await import('./output');
  const results:string[]=[];
  await page.goto('/models/configure?provider=local');
  await page.getByLabel('Preferred model · Local LLM').selectOption('pilot-qwen38-27b');
  await page.getByRole('button',{name:'Save model preferences'}).click();
  await expect(page.getByText('Preferences saved for eligible new requests. Each task still needs approval.')).toBeVisible();
  for(const width of [1440,390]){
    const module=width===1440;
    const path=module?'tests/test_normalize.lua':'tests/test_client.lua';
    await page.setViewportSize({width,height:1000});
    await page.goto('/intake/new');
    await page.getByLabel('Type of work').selectOption('coverage');
    await expect(page.getByText(/Available now: supported offline Lua bugs/)).toBeVisible();
    await page.getByLabel('Title Optional').fill(module?'Cover lowercase normalization':'Cover the client’s stored name');
    await page.getByLabel('Description', {exact:true}).fill(
      `In ${path} check how spaces are trimmed.\n${module?moduleExample:clientExample}`);
    await page.getByLabel('Add a check for existing behavior').check();
    await page.getByLabel('Expected test results').fill(module?'The normalized name is alice.':'The stored name is bob.');
    await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
    await page.getByLabel('Allow one local Qwen request to plan this task').check();
    await overflow(page);
    await page.screenshot({path:artifactPath(`everyday/intake-${width}.png`),fullPage:true});
    await page.getByRole('button',{name:'Submit request',exact:true}).dblclick();
    await expect(page.getByText('Request received and saved.',{exact:false})).toBeVisible();
    expect(JSON.parse(fixture('reviewed-offer-automatic-worker')).adapter_calls).toBe(1);
    await page.reload();
    const approve=page.getByRole('button',{name:'Approve plan',exact:true});
    await expect(approve).toBeDisabled();
    await page.getByLabel('The test examples match what I asked for.').check();
    await approve.click();
    await expect(page.getByText('Saved · Pending application',{exact:true})).toBeVisible();
    fixture('reviewed-offer-automatic-worker');
    const offer=page.getByRole('region',{name:'Certified task run approval'});
    const run=offer.getByRole('button',{name:'Approve local run',exact:true});
    await expect(run).toBeDisabled();
    await expect(offer.getByText(/Local Qwen is the saved choice/)).toBeVisible();
    await expect(offer.getByText('Publishing options')).toHaveCount(0);
    await offer.screenshot({path:artifactPath(`everyday/approval-${width}.png`)});
    await offer.getByRole('checkbox',{name:'I reviewed what this run allows.'}).check();
    await run.dblclick();
    await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading',{name:'Run permission saved · Waiting to apply'})).toBeVisible();
    fixture('reviewed-offer-automatic-tick');
    fixture('reviewed-offer-automatic-tick');
    await expect(page.getByRole('heading',{name:'Preparing approved task'})).toBeVisible();
    fixture('reviewed-offer-automatic-local-pass');
    const activity=page.locator('details[aria-label="Task model activity"]');
    await activity.locator('summary').click();
    await expect(activity.getByText(/Explicit one-task owner grant; fallback disabled/)).toBeVisible();
    await page.getByRole('link',{name:'Review changes and checks'}).click();
    const result=page.getByRole('region',{name:'Run result and checks'});
    await expect(result.getByRole('heading',{name:'Test coverage added — automated checks passed'})).toBeVisible();
    await overflow(page);
    await result.screenshot({path:artifactPath(`everyday/result-${width}.png`)});
    await page.reload();
    await expect(result.getByRole('heading',{name:'Test coverage added — automated checks passed'})).toBeVisible();
    results.push(page.url());
  }
  await page.goto(results[0]);
  await expect(page.getByRole('heading',{name:'Test coverage added — automated checks passed'})).toBeVisible();
  const history=await page.request.get('/api/real-executions').then(r=>r.json());
  expect(history.total).toBe(2);
});

test('planning provider consent shows both fallback directions on desktop and phone',async({page})=>{
  await page.route('**/api/local-work',route=>route.fulfill({json:{
    enabled:true,ready:true,codex_configured:true,codex_available:true,message:'Disposable browser fixture.',
  }}));
  const {artifactPath}=await import('./output');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    await page.goto('/intake/new');
    await page.getByLabel('Type of work').selectOption('coverage');
    await page.getByLabel('Add a check for existing behavior').check();
    await page.getByLabel('Does this change how the addon looks?').selectOption('nonvisual');
    const providers=page.getByRole('group',{name:'Planning provider'});
    const qwen=providers.getByLabel('Allow one local Qwen request to plan this task');
    const codex=providers.getByLabel('Allow one signed-in Codex turn to plan this task');
    const toCodex=providers.getByLabel(/Allow one switch to signed-in Codex/);
    const toQwen=providers.getByLabel(/Allow one switch to local Qwen/);
    await qwen.check();
    await expect(toCodex).not.toBeChecked();
    await toCodex.check();
    await expect(providers.getByText(/this permits sending your request/)).toBeVisible();
    await expect(codex).not.toBeChecked();
    await overflow(page);
    await providers.screenshot({path:artifactPath(`fallback/qwen-first-${width}.png`)});
    await codex.check();
    await expect(qwen).not.toBeChecked();
    await expect(toQwen).not.toBeChecked();
    await toQwen.check();
    await expect(providers.getByText(/Failed, partial or uncertain generation/)).toBeVisible();
    await overflow(page);
    await providers.screenshot({path:artifactPath(`fallback/codex-first-${width}.png`)});
  }
});
