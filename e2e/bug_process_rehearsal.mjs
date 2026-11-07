import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {chromium} from '@playwright/test';
import {runJourney, checkedProgress} from '../scripts/browser_acceptance.mjs';

const [, , origin, path, index] = process.argv;
const policy = JSON.parse(readFileSync(path));
const candidate = policy.cases[Number(index)];
const browser = await chromium.launch({args:['--disable-dev-shm-usage','--disable-background-networking']});
const context = await browser.newContext({serviceWorkers:'block'});
const observer = await browser.newContext({serviceWorkers:'block'});
try {
  const other = await observer.newPage();
  const events = [];
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
    record(event,detail) {events.push(event);console.log(JSON.stringify({event,...detail}));},
  });
  assert.equal(events.at(-1),'checked');
} catch(error) {
  console.error(error.cause || error);
  process.exitCode=1;
} finally {
  await observer.close();
  await context.close();
  await browser.close();
}
