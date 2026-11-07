import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFileSync, writeFileSync} from 'node:fs';
import {chromium} from '@playwright/test';
import {checkedProgress, reviewPlan, reviewRun, runJourney} from '../scripts/browser_acceptance.mjs';

const [, , origin, policyPath, phase, database, preparation] = process.argv;
const policy = JSON.parse(readFileSync(policyPath));
const opened = new Date().toISOString();
const prior = [];
const verify = (verb, candidate, action, data) => JSON.parse(execFileSync('.venv/bin/python',
  ['scripts/acceptance_policy.py', verb, '--policy', policyPath, '--database', database, '--preparation', preparation],
  {input:JSON.stringify({...data, action, choice:candidate.choice, prior, opened}), timeout:4500}));
const browser = await chromium.launch({args:['--disable-dev-shm-usage', '--disable-background-networking']});
try {
  for (const attack of ['missing control', 'dialog', 'duplicate']) {
    const context = await browser.newContext({serviceWorkers:'block'});
    const events = [];
    try {
      await assert.rejects(runJourney(context, origin, policy, policy.cases[0], {
        blockDeadline:Date.now()+30000, minimumRunMs:1000, preparationMs:1000, planningReserveMs:1000, uiMs:1800,
        async controlsReady() {
          const page = context.pages()[0];
          if (attack === 'dialog') await page.evaluate(() => alert('Unexpected approval'));
          if (attack === 'missing control') await page.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
            const css = document.createElement('style'); css.textContent = 'button[type=submit]{display:none!important}';
            document.head.append(css);
          }));
        },
        async beforeAction(action) {
          if (attack === 'duplicate' && action === 'submit') await context.pages()[0].evaluate(() =>
            fetch('/api/intake', {method:'POST', body:'{}'}).catch(() => {}));
        },
        finish: async () => assert.fail('Ineligible case ran'),
        record(event, details) { events.push({event, ...details}); },
      }), /Acceptance held/);
      assert.equal(events.at(-1).event, 'held');
      assert.equal(events.some(event => event.event === 'dispatch' && event.action === 'submit'), false);
    } finally {
      await context.close();
    }
  }
  for (const candidate of policy.cases) {
    const context = await browser.newContext({serviceWorkers:'block'});
    const observer = await browser.newContext({serviceWorkers:'block'});
    const otherPage = await observer.newPage();
    const decisions = new Map();
    context.on('request', request => {
      if (request.method() === 'POST' && /\/(?:decisions|run-approval)$/.test(request.url()))
        decisions.set(request.url().endsWith('/decisions') ? 'plan' : 'run',
          {url:request.url(), body:request.postDataJSON()});
    });
    const duplicate = async action => {
      const request = decisions.get(action);
      assert.ok(request);
      const token = await observer.request.get(origin + '/api/session').then(r => r.json());
      const headers = {Origin:origin, 'X-CSRF-Token':token.csrf_token};
      const repeated = await observer.request.post(request.url, {headers, data:request.body});
      assert.equal(repeated.status(), 200, await repeated.text());
      const stale = await observer.request.post(request.url, {headers,
        data:{...request.body, command_id:crypto.randomUUID(), binding:'0'.repeat(64)}});
      assert.equal(stale.status(), 409);
    };
    const events = [];
    try {
      await runJourney(context, origin, policy, candidate, {
        blockDeadline:Date.now()+220000, minimumRunMs:85000, preparationMs:110000, planningMs:6000,
        // Production reserves the full planning deadline; compress the fixture clock only.
        planningReserveMs:6000, pollMs:350, uiMs:7000,
        controlsReady(phaseName) {
          writeFileSync(phase, phaseName === 'implementation' ? '' : candidate.reason === 'unavailable'
            ? 'codex-off' : candidate.reason === 'local_unreachable' ? 'qwen-refused' : '');
        },
        async beforeAction(action, data) {
          verify('admit', candidate, action, data);
          if (action === 'plan') {
            await otherPage.goto(origin + '/intake/' + data.intake);
            const item = await context.request.get(origin + '/api/intake/' + data.intake).then(r => r.json());
            const revision = item.proposals[0].task_contract.scenario_grounding.model_selection.policy_revision;
            for (const change of [
              value => value.proposals[0].task_contract.writable_paths.push('other.lua'),
              value => value.proposals[0].task_contract.verification_commands[0].argv.push('extra'),
              value => value.proposals[0].content.scope.push('Install an addon'),
              value => value.proposals[0].task_contract.scenario_grounding.basis = 'demo',
              value => value.provider_activity[0].outcome = 'interrupted_unknown',
              value => value.provider_activity[0].outcome = 'incomplete_output',
              value => value.created_at = '2000-01-01T00:00:00Z',
              value => value.jobs[0].attempts = 2,
            ]) {
              const changed = structuredClone(item);
              change(changed);
              assert.throws(() => reviewPlan(changed, candidate, data.intake, revision, Date.now()));
            }
          }
          if (action === 'run') {
            await duplicate('plan');
            const item = await context.request.get(origin + '/api/intake/' + data.intake).then(r => r.json());
            const task = item.tasks.find(row => row.kind === 'task');
            reviewRun(data.offer, task, data.proposal, item.decisions[0], candidate, policy);
            for (const change of [
              value => value.available = false,
              value => value.scope_approval_id = 'stale',
              value => value.offer.budget.publication = 'draft-pr',
              value => value.receipt = {id:'duplicate'},
              value => value.provider.model = 'wrong',
              value => value.offer.paths.push('unapproved.lua'),
            ]) {
              const changed = structuredClone(data.offer);
              change(changed);
              assert.throws(() => reviewRun(changed, task, data.proposal, item.decisions[0], candidate, policy));
            }
          }
        },
        async finish(data) {
          await duplicate('run');
          const row = await context.request.get(origin + '/api/intake/' + data.intake + '/tasks/' + data.task + '/execution').then(r => r.json());
          assert.equal(checkedProgress(row), true);
          assert.equal(checkedProgress({...row, stage:'settling'}), false);
          assert.throws(() => checkedProgress({...row, stage:'failed'}));
          assert.throws(() => checkedProgress({...row, stage:'recovering'}));
          const result = verify('closeout', candidate, 'finish', data);
          prior.push(...result.attempts);
          return {...result, restoration:'controlled fixture'};
        },
        record(event, details) { events.push(event); console.log(JSON.stringify({case:candidate.choice,event,...details})); },
      });
      assert.equal(events.filter(event => event === 'reviewed').length, 2);
      assert.equal(events.at(-1), 'checked');
    } finally {
      await observer.close();
      await context.close();
    }
  }
} catch (error) {
  console.error(error.cause || error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
