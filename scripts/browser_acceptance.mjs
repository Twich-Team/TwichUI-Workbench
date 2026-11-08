/** Acceptance-only browser driver. All mutations come from the rendered Workbench UI. */
import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value);
const same = (a, b) => isDeepStrictEqual(a, b);
function check(condition, reason) {
  if (!condition) throw new Error(reason);
}
function exact(a, b, reason) { check(same(a, b), reason); }
function without(value, ...keys) {
  return Object.fromEntries(Object.entries(value).filter(([key]) => !keys.includes(key)));
}
function originalRequest(value, expected) {
  check(uuid(value.request_id), 'intake identity missing');
  const submitted = without(value, 'request_id');
  // Response schemas materialize optional nulls. No non-null extra request data is allowed.
  for (const key of ['local_planning', 'supported_task', 'regression_tests']) if (submitted[key] === null) delete submitted[key];
  if (submitted.regression_tests?.text_example === null)
    submitted.regression_tests = without(submitted.regression_tests, 'text_example');
  exact(submitted, expected, 'intake request changed');
}

const proposalIdentities = item => item.proposals.map(p=>({id:p.id,binding:p.binding,revision:p.revision}))
  .sort((a,b)=>a.revision-b.revision);
const priorProposals = reply => [...(reply.prior_proposals || []),
  {id:reply.question,binding:reply.question_binding,revision:reply.revision || 1}];
export function reviewClarification(item, candidate, reply) {
  const phases=reply.revision || 1;
  check(Number.isInteger(phases) && phases>=1 && candidate.request.kind === 'issue' &&
    uuid(reply.intake) && hex(reply.question_binding) && uuid(reply.question) &&
    typeof reply.answer === 'string' && reply.answer.trim().length>0 && reply.answer.length<=1600,
    'clarification binding or answer unavailable');
  check(item.id===reply.intake && ['waiting_for_information','failed'].includes(item.status) &&
    item.revision===phases && item.proposals.length===phases &&
    item.proposals.find(p=>p.revision===phases)?.task_contract.scope_missing.length>0 &&
    !item.tasks.length && !item.decisions.length,'clarification task changed or already continued');
  exact(proposalIdentities(item),priorProposals(reply),'clarification proposal history changed');
  exact(item.clarifications,reply.prior_clarifications || [],'clarification answer history changed');
  originalRequest(item.original,candidate.request);
  check(item.provider_activity?.length===phases && item.provider_activity.every(a=>
    a.purpose==='planning' && a.backend===candidate.provider && a.actual_model===candidate.model &&
    a.requested_model===candidate.model && a.outcome==='completed') && item.jobs.length===phases &&
    item.jobs.every(j=>j.status==='completed'&&j.attempts===1),
    'clarification investigation is unsettled or provider changed');
  return item;
}

export function reviewPlan(item, candidate, identity, revision, now, reply) {
  const phases = reply ? (reply.revision || 1)+1 : 1;
  check(item.id === identity && item.revision === phases && item.proposals.length === phases &&
    item.clarifications.length === phases-1 && item.scope_history.length === 1, 'unexpected intake revision or history');
  originalRequest(item.original, candidate.request);
  if (reply) {
    check(item.id===reply.intake && item.clarifications.at(-1).text===reply.answer &&
      item.clarifications.at(-1).revision===phases,'clarification history or answer changed');
    exact(proposalIdentities(item).slice(0,-1),priorProposals(reply),'prior proposal history changed');
    exact(item.clarifications.slice(0,-1),reply.prior_clarifications || [],'prior answer history changed');
  }
  const consentTime = reply ? item.clarifications.at(-1).created_at : item.created_at;
  check(Date.parse(consentTime) <= now && now - Date.parse(consentTime) < 30 * 60000,
    'planning approval expired or time unavailable');
  check(item.human_scope.visual_requirement === 'nonvisual' &&
    item.human_scope.stage === 'implementation' && item.human_scope.origin === 'human',
    'human scope changed');
  const plan = item.proposals.find(plan => plan.revision === phases);
  check(uuid(plan.id) && hex(plan.binding) && plan.revision === item.revision &&
    plan.scope_revision === item.human_scope.revision && plan.basis === 'preparation' &&
    plan.model === candidate.model && !plan.research_only, 'plan identity or provider changed');
  for (const key of ['required_artifacts', 'scope_conflicts', 'artifacts', 'approval_blockers'])
    exact(plan[key], [], 'plan prerequisites or attachments changed');
  exact(plan.human_scope, item.human_scope, 'plan scope revision changed');
  const bug = candidate.request.kind === 'issue';
  const {behavioral_evidence: behavior, scenario_grounding: ground, ...contract} = plan.task_contract;
  if (bug) {
    check(contract.category === 'lua-offline-bugfix' && !contract.scope_missing.length &&
      !contract.execution_authorized && !contract.publication_authorized &&
      hex(contract.bug_evidence?.review_digest) && contract.bug_evidence.plan.reproducer.observations.length >= 3 &&
      contract.writable_paths.every(path => candidate.writablePaths.includes(path)),
    'repair scope, behavior checks or publication boundary changed');
  } else {
    exact(plan.content, candidate.content, 'plan wording or proposed operations changed');
    exact(contract, without(candidate.contract, 'behavioral_evidence'), 'test paths, evidence, commands or limits changed');
    const [scenario] = behavior.scenarios;
    check(behavior.scenarios.length === 1 && /^[a-z][a-z0-9-]{0,47}$/.test(scenario.id) &&
      hex(behavior.input_sha256) && hex(behavior.review_digest), 'example identity changed');
    exact(without(behavior, 'input_sha256', 'review_digest', 'scenarios'),
      without(candidate.contract.behavioral_evidence, 'input_sha256', 'review_digest', 'scenarios'),
      'example provenance changed');
    exact(without(scenario, 'id'), without(candidate.contract.behavioral_evidence.scenarios[0], 'id'),
      'example operation, setup, file or expected result changed');
  }
  check(hex(ground.policy_sha256) && hex(ground.evidence_sha256), 'preparation policy binding missing');
  exact(ground, {evidence_sha256: bug ? ground.evidence_sha256 : candidate.evidenceSha, policy_sha256: ground.policy_sha256,
    basis: candidate.provider, ...(candidate.reason ? {fallback_reason: candidate.reason} : {}),
    model_selection: {policy_revision: revision, model: candidate.model}}, 'grounding or provider route changed');
  check(item.provider_activity?.length === phases && item.provider_activity.every(attempt =>
    attempt.purpose === 'planning' && attempt.backend === candidate.provider &&
    attempt.requested_model === candidate.model && attempt.actual_model === candidate.model &&
    attempt.outcome === 'completed'), 'planning attempt is uncertain or unexpected');
  const jobs = item.jobs.filter(job => job.kind === 'planning');
  check(jobs.length === phases && jobs.every(job => job.status === 'completed' && job.attempts === 1) &&
    new Set(jobs.map(job => job.revision)).size === phases, 'duplicate or unsettled planning');
  return plan;
}

export function reviewRun(view, task, plan, approval, candidate, policy) {
  check(view.available && view.runtime_ready && view.local_only && !view.receipt && !view.approval_hold &&
    hex(view.binding) && view.scope_approval_id === approval.id && task.proposal_id === plan.id &&
    !task.scope_review_required, 'certified offer stale, already used or mismatched');
  exact(view.scope, plan, 'reviewed scope changed before implementation');
  exact(view.provider, {backend: candidate.provider, model: candidate.model, effort: null,
    fallback_reason: candidate.reason}, 'implementation provider changed');
  const offer = view.offer;
  check(uuid(offer.id) && hex(offer.fingerprint), 'certified offer identity missing');
  exact(offer, {id: offer.id, fingerprint: offer.fingerprint, title: candidate.request.title,
    repository: 'Twich-Team/TwichUI', base_branch: 'main', base: policy.base,
    paths: plan.task_contract.writable_paths, checks: plan.task_contract.verification_commands,
    limits: policy.limits, budget: {...policy.budget, offer_id: offer.id, offer_fingerprint: offer.fingerprint}},
  'implementation files, commands, publication or budget changed');
}

export function checkedProgress(row) {
  check(!['failed','held','blocked','interrupted','cancelled','recovering'].includes(row.stage) &&
    row.runs.length <= 1 && !row.publication_allowed, 'implementation checks, completion or restoration held');
  return row.stage === 'checked' && row.session?.status === 'completed' && row.runs.length === 1 &&
    row.runs[0].status === 'checked' && row.runs[0].verification === 'passed';
}

export async function runJourney(context, origin, policy, candidate, options) {
  const {beforeAction, controlsReady, finish, record, blockDeadline} = options;
  const now = options.now || Date.now;
  const page = await context.newPage();
  const uiMs = options.uiMs || 7000;
  page.setDefaultTimeout(uiMs);
  page.setDefaultNavigationTimeout(uiMs);
  let armed, violation;
  const hold = reason => { violation ??= reason; };
  page.on('dialog', dialog => { hold('unexpected browser dialog'); void dialog.dismiss().catch(() => {}); });
  context.on('page', popup => { hold('unexpected browser page'); void popup.close().catch(() => {}); });
  await context.routeWebSocket('**/*', ws => { hold('unexpected websocket'); void ws.close(); });
  await context.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin !== origin || !['GET', 'HEAD'].includes(req.method()) &&
      (!armed || armed.used || url.pathname !== armed.path || url.search || req.method() !== armed.method)) {
      hold('unexpected origin or mutation');
      return route.abort();
    }
    if (['GET', 'HEAD'].includes(req.method())) return route.continue();
    const gate = armed;
    gate.used = true; // Fence duplicate requests before any asynchronous check.
    try {
      const body = req.postDataJSON();
      check(!violation && now() < gate.deadline, 'browser decision window closed');
      await gate.validate(body);
      check(!violation && now() < gate.deadline, 'browser decision window closed');
      record('dispatch', {action: gate.action, command: body.command_id ?? body.request_id ?? null,
        proposal: body.proposal_id ?? null, binding: body.binding ?? null,
        offer: body.run_budget?.offer_id ?? null, budget: body.run_budget?.offer_fingerprint ?? null});
    } catch (error) {
      hold(error.message);
      return route.abort();
    }
    return route.continue(); // Original browser request, headers, CSRF token and body.
  });
  const ensure = () => check(!violation && now() < blockDeadline, violation || 'case deadline expired');
  const json = async path => {
    ensure();
    const response = await context.request.get(origin + path, {timeout: uiMs, maxRedirects: 0});
    check(response.ok(), 'read-only app check failed');
    return response.json();
  };
  const eventually = async (query, test, milliseconds) => {
    const end = Math.min(blockDeadline, now() + milliseconds);
    do {
      ensure();
      const value = await query();
      if (test(value)) return value;
      await new Promise(resolve => setTimeout(resolve, options.pollMs || 500));
    } while (now() < end);
    throw new Error('bounded wait expired');
  };
  const act = async (action, method, path, locator, validate, budget = uiMs) => {
    ensure();
    const gate = {action, method, path, validate, deadline: Math.min(blockDeadline, now() + budget), used: false};
    check(!armed, 'browser action already armed');
    armed = gate;
    try {
      const response = await Promise.all([
        page.waitForResponse(r => r.url() === origin + path && r.request().method() === method, {timeout: budget}),
        locator.click({timeout: uiMs}),
      ]).then(([result]) => result);
      ensure();
      check(gate.used && response.ok(), 'browser decision did not settle successfully');
      const saved = await response.json();
      record('response', {action, status: response.status(), id: saved.id || null});
      return saved;
    } finally {
      armed = undefined;
    }
  };
  const button = name => page.getByRole('button', {name, exact: true});
  const getPolicy = async () => (await json('/api/models')).policy;
  const settings = async (direction, setModels) => {
    await page.goto(origin + '/models/configure?provider=local');
    const previous = await getPolicy();
    const expected = structuredClone(previous);
    if (setModels) {
      for (const [key, name] of [['local', 'Local LLM'], ['codex', 'Codex']]) {
        await page.getByLabel('Configure provider').selectOption(key);
        await page.getByRole('checkbox', {name:'Allow this provider in routing'}).check();
        if (expected.providers[key].model !== policy.models[key] || expected.providers[key].effort !== null) {
          await page.getByText('Advanced model identifier', {exact:true}).click();
          const field = page.getByLabel('Manual model identifier · ' + name);
          await field.fill('');
          await field.fill(policy.models[key]);
        }
        Object.assign(expected.providers[key], {model:policy.models[key], enabled:true, effort:null});
      }
    }
    await page.getByText('Routing preferences', {exact:true}).click();
    if (previous.test_fallback !== direction) await page.getByLabel(/Fallback for eligible (tests|work)/).selectOption(direction);
    expected.test_fallback = direction;
    if (direction === 'both') {
      if (previous.substitution !== 'configured_fallback_only')
        await page.getByLabel('Provider fallback').selectOption('configured_fallback_only');
      expected.substitution = 'configured_fallback_only';
      await page.getByText('Permitted fallback conditions', {exact:true}).click();
      const label = 'Provider unavailable or disabled';
      await page.getByRole('checkbox', {name:label}).check();
      if (!expected.fallback_on.includes('unavailable')) expected.fallback_on.push('unavailable');
      await page.getByRole('checkbox', {name:'Exhausted capacity'}).uncheck();
      expected.fallback_on = expected.fallback_on.filter(x => x !== 'capacity');
    }
    if (!same(expected, previous)) {
      await act('preferences', 'PUT', '/api/models/policy', button('Save model preferences'), async body => {
        exact(await getPolicy(), previous, 'model policy changed during review');
        exact(body, expected, 'model preference mutation differs from sealed route');
      });
    }
    const saved = await getPolicy();
    exact(without(saved, 'revision'), without(expected, 'revision'), 'saved policy differs');
    return saved;
  };
  try {
    const desired = candidate.reason ? 'both' : 'off';
    const reply = options.clarification;
    const saved = reply ? await getPolicy() : await settings(desired, true);
    if (reply) check(!candidate.reason && saved.revision === reply.policy_revision &&
      saved.test_fallback === 'off' && saved.providers[candidate.provider].model === candidate.model,
    'clarification saved provider policy changed');
    await controlsReady('planning');
    const bug = candidate.request.kind === 'issue';
    if (!reply) {
    await page.goto(origin + '/intake/new?kind=' + candidate.request.kind);
    await page.getByLabel('Title', {exact:false}).fill(candidate.request.title);
    await page.getByLabel('Description', {exact:false}).fill(candidate.request.description);
    if (!bug) {
      await page.getByRole('checkbox', {name:'Add a check for existing behavior'}).check();
      await page.getByLabel('Expected test results').fill(candidate.request.regression_tests.acceptance[0]);
      await page.getByText('Optional test details', {exact:true}).click();
      await page.getByLabel('Existing test paths').fill(candidate.request.regression_tests.test_paths[0]);
    }
    await page.getByLabel('Does this request change the interface?').selectOption('nonvisual');
    const local = candidate.request.local_planning.startsWith('qwen-');
    await page.getByRole('checkbox', {name:local ? (bug ? /Allow local Qwen to investigate/ : /Allow one local Qwen request/) : (bug ? /Allow signed-in Codex to investigate/ : /Allow one signed-in Codex turn/)}).check();
    if (candidate.reason) await page.getByRole('checkbox', {name:local ?
      /Allow one switch to signed-in Codex/ : /Allow one switch to local Qwen/}).check();
    }
    const capacity = async () => {
      exact(await getPolicy(), saved, 'saved provider policy changed');
      const work = await json('/api/local-work');
      check(work.ready && work.enabled && work.headroom_bytes >= 72 * 1048576, 'storage or worker not ready');
      return work;
    };
    let requestId, identity;
    if (reply) {
      identity = reply.intake;
      const url = "/api/intake/" + identity;
      await page.goto(origin + "/intake/" + identity);
      const current = reviewClarification(await json(url), candidate, reply);
      requestId = current.original.request_id;
      await page.getByLabel("Your answer", {exact:true}).fill(reply.answer);
      await act("clarify", "POST", url + "/clarification", button("Send reply and allow follow-up"), async body => {
        exact(await json(url), current, "clarification changed before reply");
        exact(without(body, "command_id"), {revision:reply.revision || 1, text:reply.answer, continue_investigation:true},
          "clarification command differs from owner answer");
        check(uuid(body.command_id), "clarification command identity missing");
        await capacity();
        await beforeAction("clarify", {intake:identity, clarification:reply});
      });
    } else {
    const opened = await act('submit', 'POST', '/api/intake', button('Submit request'), async body => {
      originalRequest(body, candidate.request);
      const work = await capacity();
      check((!bug || work.bug_investigation) && work.codex_available === (candidate.choice !== 'codex-with-local-fallback'), 'unexpected connector readiness');
      check(blockDeadline - now() >= (options.minimumRunMs ?? 60*60000) + (options.preparationMs ?? 10*60000) + (options.planningReserveMs ?? 150000) + (bug ? options.reviewMs ?? 0 : 0),
        'planning and run no longer fit the case budget');
      await beforeAction('submit', {request: body});
      requestId = body.request_id;
    });
    check(uuid(opened.id) && opened.original.request_id === requestId, 'intake response does not match submitted case');
    identity = opened.id;
    await page.waitForURL(origin + '/intake/' + identity + '?received=1', {timeout:uiMs});
    }
    const url = '/api/intake/' + identity;
    const readItem = () => json(url);
    const complete = await eventually(readItem, item => {
      check(['queued','investigating','completed'].includes(item.status), 'planning held or uncertain');
      return item.status === 'completed';
    }, options.planningMs ?? 150000);
    const plan = reviewPlan(complete, candidate, identity, saved.revision, now(), reply);
    check(!complete.decisions.length && !complete.tasks.length && complete.original.request_id === requestId,
      'unexpected prior plan approval or task');
    if (bug) {
      check(typeof options.reviewBug === 'function', 'independent bug plan review is unavailable');
      await options.reviewBug({intake:identity, plan});
      ensure();
      exact(await readItem(), complete, 'plan changed during independent bug review');
    }
    await controlsReady('implementation'); // Restore any controlled outage before approving work.
    if (candidate.reason) await page.reload();
    // The app preserves the previously viewed revision and offers an explicit
    // navigation control when a clarification produces a newer plan.
    if (reply) {
      await button('Open current proposal').click();
    }
    await page.getByText('Add a note or request changes', {exact:true}).click();
    await page.getByLabel('Note', {exact:false}).fill(policy.note);
    const review = bug ? plan.task_contract.bug_evidence : plan.task_contract.behavioral_evidence;
    await page.getByRole('checkbox', {name:bug ?
      'The reproduction and expected behavior match my report.' : 'The test examples match what I asked for.'}).check();
    const scope = await act('plan', 'POST', url + '/decisions', button('Approve plan'), async body => {
      await capacity();
      const fresh = await readItem();
      exact(fresh, complete, 'plan changed after review');
      exact(without(body, 'command_id'), {proposal_id: plan.id, binding: plan.binding, scope_revision: plan.scope_revision,
        action: 'approve', note: policy.note, expectation_review: review.review_digest},
      'plan command differs from reviewed scope');
      check(uuid(body.command_id), 'plan command identity missing');
      await beforeAction('plan', {intake:identity, proposal:plan});
      record('reviewed', {action:'plan', intake:identity, proposal:plan.id, planSha:digest(plan),
        provider:candidate.provider, checks:'exact scope, example, commands, source, budget and routing'});
    });
    const prepared = await eventually(readItem, item => {
      const receipt = item.decisions.find(d => d.id === scope.id);
      check(item.decisions.length <= 1 && (!receipt || ['pending','applied'].includes(receipt.status)) &&
        !item.jobs.some(job => job.status === 'failed'), 'plan decision or independent preparation held');
      return receipt?.status === 'applied' && item.tasks.length > 0 && item.jobs.every(job => job.status === 'completed');
    }, options.preparationMs ?? 600000);
    const tasks = prepared.tasks.filter(t => t.kind === 'task');
    check(tasks.length === 1 && tasks[0].id === 'intake-' + identity + '-' + plan.revision + '-implementation' && prepared.decisions[0].binding === plan.binding,
      'prepared task identity changed');
    const [task] = tasks;
    const runPath = url + '/tasks/' + task.id + '/run-approval';
    const view = await eventually(() => json(runPath), row => {
      check(!row.receipt && !row.approval_hold, 'prior run decision or stale offer');
      return row.available;
    }, options.preparationMs ?? 600000);
    reviewRun(view, task, plan, prepared.decisions[0], candidate, policy);
    const panel = page.getByRole('region', {name:'Certified task run approval'});
    await panel.getByText('Technical run details', {exact:true}).click();
    await panel.getByLabel('Run approval note').fill(policy.note);
    await panel.getByRole('checkbox', {name:'I reviewed what this run allows.'}).check();
    const run = await act('run', 'POST', runPath, button(candidate.provider === 'codex' ? 'Approve Codex run' : 'Approve local run'),
      async body => {
        const work = await capacity();
        check(work.codex_available, 'connector changed after planning');
        exact(await json(runPath), view, 'certified offer changed after review');
        const current = await readItem();
        exact(reviewPlan(current, candidate, identity, saved.revision, now(), reply), plan, 'plan changed before run');
        exact(current.tasks, prepared.tasks, 'task version changed before run');
        check(current.decisions.length === 1 && current.decisions[0].id === scope.id &&
          current.decisions[0].status === 'applied', 'reviewed approval changed');
        const progress = await json(url + '/tasks/' + task.id + '/execution');
        check(progress.current_approval && !progress.session && !progress.runs.length && !progress.publication_allowed,
          'an earlier run, recovery, or publication exists');
        check(blockDeadline - now() >= (options.minimumRunMs ?? 60*60000), 'run and restoration do not fit the case budget');
        exact(without(body, 'command_id'), {scope_approval_id:scope.id, binding:view.binding,
          run_budget:view.offer.budget, note:policy.note}, 'run command differs from reviewed offer');
        check(uuid(body.command_id), 'run command identity missing');
        await beforeAction('run', {intake:identity, task:task.id, proposal:plan, offer:view});
        record('reviewed', {action:'run', intake:identity, task:task.id, approval:scope.id, provider:candidate.provider,
          offer:view.offer.id, offerSha:digest(view.offer), checks:'current plan, exact certified offer, local-only budget'});
      });
    const progress = await eventually(() => json(url + '/tasks/' + task.id + '/execution'),
      checkedProgress, options.minimumRunMs ?? 60*60000);
    const [execution] = progress.runs;
    check(execution.current_task && execution.candidate_id, 'checked result belongs to another task version');
    const result = await finish({intake:identity, task:task.id, execution:execution.id, run:run.id});
    if (candidate.choice === policy.cases.at(-1).choice) await settings('off', false);
    record('checked', {intake:identity, task:task.id, execution:execution.id, ...result});
    return result;
  } catch (error) {
    // Playwright errors may contain page text. Receipts only contain fixed text or error classes.
    const reason = violation || (error.name === 'TimeoutError' ? 'missing control or browser timeout' : error.message.split('\n')[0]);
    record('held', {reason: /^[a-zA-Z0-9 ,._-]{1,150}$/.test(reason) ? reason : 'unexpected browser state'});
    throw new Error('Acceptance held. See the bounded decision receipt.', {cause:error});
  } finally {
    await page.close();
  }
}
