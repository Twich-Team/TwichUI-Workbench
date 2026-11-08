import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {chromium} from '@playwright/test';
import {runJourney, checkedProgress} from '../scripts/browser_acceptance.mjs';

const [, , origin, path, index] = process.argv;
const policy = JSON.parse(readFileSync(path));
const conflict = index === 'conflict';
const fault = conflict || index === 'fault';
const candidate = fault ? {...policy.cases[0],request:{...policy.cases[0].request,
  title:policy.cases[0].request.title+(conflict?' · controlled conflicting outcomes':' · controlled malformed response')}} : policy.cases[Number(index)];
const browser = await chromium.launch({args:['--disable-dev-shm-usage','--disable-background-networking']});
let context = await browser.newContext({serviceWorkers:'block'});
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
  let clarification;
  if (candidate.clarification && !fault) {
    const preparationEvents=[];
    await assert.rejects(runJourney(context, origin, policy, candidate, {
      blockDeadline:Date.now()+140000,minimumRunMs:40000,preparationMs:55000,
      planningReserveMs:1000,planningMs:7000,uiMs:7000,pollMs:100,
      async controlsReady(){}, async beforeAction(action){assert.equal(action,'submit');},
      record(event,detail){preparationEvents.push({event,...detail});},
    }));
    const identity=preparationEvents.find(e=>e.event==='response'&&e.action==='submit').id;
    const item=await observer.request.get(origin+'/api/intake/'+identity).then(r=>r.json());
    assert.equal(item.status,'waiting_for_information');
    assert.equal(item.provider_activity.length,1);
    assert.equal(item.provider_activity[0].outcome,'completed');
    await other.goto(origin+'/intake/'+identity);
    await other.getByLabel('Your answer',{exact:true}).waitFor();
    await other.screenshot({path:join(dirname(path),'clarification-desktop.png'),fullPage:true});
    await other.setViewportSize({width:390,height:844});
    await other.screenshot({path:join(dirname(path),'clarification-phone.png'),fullPage:true});
    await other.setViewportSize({width:1280,height:720});
    const saved=await observer.request.get(origin+'/api/models').then(r=>r.json());
    clarification={intake:identity,question:item.proposals[0].id,question_binding:item.proposals[0].binding,
      policy_revision:saved.policy.revision,answer:'Normalize it each time.'};
    await context.close();
    context=await browser.newContext({serviceWorkers:'block'});
    const secondEvents=[];
    await assert.rejects(runJourney(context,origin,policy,candidate,{
      clarification,blockDeadline:Date.now()+140000,minimumRunMs:40000,preparationMs:55000,
      planningReserveMs:1000,planningMs:7000,uiMs:7000,pollMs:100,
      async controlsReady(){},async beforeAction(action){assert.equal(action,'clarify');},
      record(event,detail){secondEvents.push({event,...detail});},
    }));
    const second=await observer.request.get(origin+'/api/intake/'+identity).then(r=>r.json());
    assert.equal(second.status,'failed'); // Historical one-reply limitation, injected by controlled worker.
    assert.equal(second.revision,2);
    assert.equal(second.provider_activity.length,2);
    assert.ok(second.provider_activity.every(a=>a.outcome==='completed'));
    clarification={intake:identity,revision:2,question:second.proposals[0].id,
      question_binding:second.proposals[0].binding,policy_revision:saved.policy.revision,
      prior_proposals:second.proposals.filter(p=>p.revision<2).map(p=>({id:p.id,binding:p.binding,revision:p.revision})),
      prior_clarifications:second.clarifications,
      answer:'Technical context: the existing operation can be checked offline; no new owner behavior choice.'};
    await context.close();
    context=await browser.newContext({serviceWorkers:'block'});
  }
  const interruptScope=!fault&&Number(index)===1&&policy.reproduction_mode==='source-grounded-intent-v1';
  let interrupted=false,approvedScope;
  const options={
    clarification,
    blockDeadline:Date.now()+140000, minimumRunMs:40000, preparationMs:55000,
    planningReserveMs:1000, planningMs:7000, uiMs:7000, pollMs:100,
    async controlsReady() {},
    async reviewBug({intake,plan}) {
      if(plan.task_contract.bug_intent) assert.deepEqual(plan.task_contract.bug_intent.plan,candidate.intent);
      else assert.deepEqual(plan.task_contract.bug_evidence.plan,candidate.reviewed);
      await other.goto(origin+'/intake/'+intake);
      if(plan.task_contract.bug_intent) {
        await other.screenshot({path:join(dirname(path),'scope-'+index+'-desktop.png'),fullPage:true});
        await other.setViewportSize({width:390,height:844});
        await other.screenshot({path:join(dirname(path),'scope-'+index+'-phone.png'),fullPage:true});
        await other.setViewportSize({width:1280,height:720});
      }
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
      if(event==='response'&&detail.action==='plan'&&interruptScope&&!interrupted) {
        interrupted=true;approvedScope=detail.id;throw new Error('controlled driver closure after scope');
      }
      console.log(JSON.stringify({event,...detail}));
    },
  };
  if(interruptScope) {
    await assert.rejects(runJourney(context,origin,policy,candidate,options));
    assert(interrupted&&approvedScope&&submitted);
    await context.close();context=await browser.newContext({serviceWorkers:'block'});
    let item=await observer.request.get(origin+'/api/intake/'+submitted).then(r=>r.json());
    if(candidate.provider==='local') {
      const end=Date.now()+15000;
      while(!item.jobs.some(j=>j.kind==='behavioral_preparation'&&j.status==='failed')) {
        assert(Date.now()<end,'Controlled construction must settle before renewal');
        await new Promise(resolve=>setTimeout(resolve,100));
        item=await observer.request.get(origin+'/api/intake/'+submitted).then(r=>r.json());
      }
      await other.goto(origin+'/intake/'+submitted);
      const response=await Promise.all([
        other.waitForResponse(r=>r.url()===origin+'/api/intake/'+submitted+'/retry'&&r.request().method()==='POST'),
        other.getByRole('button',{name:'Rebuild checks',exact:true}).click(),
      ]).then(([r])=>r);
      assert.equal(response.status(),200,await response.text());
      item=await observer.request.get(origin+'/api/intake/'+submitted).then(r=>r.json());
    }
    const settledPreparations=item.provider_activity.filter(a=>a.purpose==='check_preparation'&&a.outcome==='failed').map(a=>a.id);
    const selected=item.proposals[0];
    const saved=await observer.request.get(origin+'/api/models').then(r=>r.json());
    await runJourney(context,origin,policy,candidate,{...options,
      resumeScope:{intake:submitted,approval:approvedScope,proposal:selected.id,binding:selected.binding,revision:selected.revision,policy_revision:saved.policy.revision,settled_preparations:settledPreparations},
      async reviewResumedScope({scope,plan}) {assert.equal(scope.id,approvedScope);assert.equal(plan.id,selected.id);},
    });
    assert.equal(events.filter(e=>e==='resumed-applied-scope').length,1);
  } else await runJourney(context,origin,policy,candidate,options);
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
    assert.equal(result.jobs[0].diagnostics.validation_locations,conflict?
      (candidate.provider==='codex'?'action:invalid_outcome':
      'draft.Diagnosed.diagnosis:missing; draft.Diagnosed.question:extra_forbidden; draft.Diagnosed.limitation:extra_forbidden; draft.Inspect.inspection:missing'):'finding:string_too_long');
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
