import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from '@playwright/test';
import {runJourney, checkedProgress} from '../scripts/browser_acceptance.mjs';

const [, , origin, path, index] = process.argv;
const policy = JSON.parse(readFileSync(path));
const fault = index === 'fault';
const candidate = fault ? {...policy.cases[0],request:{...policy.cases[0].request,
  title:policy.cases[0].request.title+' · controlled malformed response'}} : policy.cases[Number(index)];
const browser = await chromium.launch({args:['--disable-dev-shm-usage','--disable-background-networking']});
const context = await browser.newContext({serviceWorkers:'block'});
const observer = await browser.newContext({serviceWorkers:'block'});
let polling=false, statusReads=0, observerRead=Promise.resolve();
const poller=setInterval(()=>{
  if(polling)return;
  polling=true;
  observerRead=(async()=>{
    await observer.request.get(origin+'/api/local-work',{timeout:2000});
    await observer.request.get(origin+'/api/intake',{timeout:2000});
    statusReads++;
  })().finally(()=>{polling=false;});
  observerRead.catch(()=>{});
},200);
let submitted;
const events = [];
try {
  const other = await observer.newPage();
  await runJourney(context, origin, policy, candidate, {
    blockDeadline:Date.now()+140000, minimumRunMs:40000, preparationMs:55000,
    planningReserveMs:1000, planningMs:7000, uiMs:7000, pollMs:100,
    async controlsReady() {},
    async reviewBug({intake,plan}) {
      assert.deepEqual(plan.task_contract.bug_evidence.plan,candidate.reviewed);
      await other.goto(origin+'/intake/'+intake);
    },
    async beforeAction(action,data) {
      if(action === 'run') {
        const item=await observer.request.get(origin+'/api/intake/'+data.intake).then(r=>r.json());
        assert.equal(item.decisions.length,1);
        assert.equal(item.decisions[0].status,'applied');
      }
    },
    async finish(data) {
      const response=await observer.request.get(origin+'/api/intake/'+data.intake+'/tasks/'+data.task+'/execution');
      const result=await response.json();
      assert.equal(checkedProgress(result),true);
      const review=await observer.request.get(origin+'/api/real-executions/'+data.execution).then(r=>r.json());
      assert.equal(review.result.verification,'passed');
      assert.ok(review.result.files.some(file=>file.path.startsWith('!!!TwichUI/')));
      await other.reload();
      const saved=await observer.request.get(origin+'/api/real-executions').then(r=>r.json());
      assert.equal(saved.total,Number(index)+1);
      return {restoration:'controlled provider fixture',candidate:result.runs[0].candidate_id};
    },
    record(event,detail) {
      events.push(event);
      if(event==='response'&&detail.action==='submit') submitted=detail.id;
      console.log(JSON.stringify({event,...detail}));
    },
  });
  assert.equal(fault,false,'Malformed response must not become an approved task');
  assert.equal(events.at(-1),'checked');
} catch(error) {
  if(fault&&submitted&&events.at(-1)==='held') {
    const result=await observer.request.get(origin+'/api/intake/'+submitted).then(r=>r.json());
    assert.equal(result.status,'failed');
    assert.equal(result.proposals.length,0);
    assert.equal(result.tasks.length,0);
    assert.equal(result.decisions.length,0);
    assert.equal(result.jobs[0].diagnostics.generation_state,'finished');
    assert.equal(result.jobs[0].diagnostics.validation_locations,'finding:string_too_long');
    assert.ok(result.jobs[0].diagnostics.evidence_id);
    assert.equal(result.provider_activity[0].outcome,'failed');
    assert.equal(JSON.stringify(result).includes('PRIVATE_REJECTED_CONTENT'),false);
  } else {
    console.error(error.cause || error);
    process.exitCode=1;
  }
} finally {
  clearInterval(poller);
  await observerRead.catch(()=>{});
  assert.ok(statusReads>0,'Second client must poll while the normal driver is active');
  await observer.close();
  await context.close();
  await browser.close();
}
