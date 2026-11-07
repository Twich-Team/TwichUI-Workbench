/** Operator entry point for a newly approved, sealed Workbench stage. */
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {closeSync, fsyncSync, lstatSync, openSync, readFileSync, readlinkSync, realpathSync, writeSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {runJourney} from './browser_acceptance.mjs';

const release = dirname(dirname(fileURLToPath(import.meta.url)));
const stage = dirname(release);
const ops = join(stage, 'ops');
const evidence = join(stage, 'artifacts', 'acceptance');
const origin = 'http://127.0.0.1:9487';
const connector = '/run/twichui-workbench-codex/connector.sock';
const sha = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const helper = '/usr/local/sbin/twichui-workbench-admin';
const helperSha = '1fd4a561188560242d5cb5df9bd583d8c922cfa4282e4bd1e46b8dca04bc3afb';
const minute = 60000;
const started = Date.now();
const monotonic = performance.now();
const now = () => Math.max(Date.now(), started + performance.now() - monotonic);
process.chdir(stage);
function need(condition, reason) {
  if (!condition) throw new Error(reason);
}
const host = (path, args, input, timeout = 4000) =>
  execFileSync(path, args, {input, encoding:'utf8', timeout, maxBuffer:128*1024, stdio:['pipe','pipe','pipe']}).trim();
const admin = request => host('/usr/bin/sudo', ['-n', helper], JSON.stringify(request), 270000);
const unit = role => JSON.parse(admin({op:'inspect', target:'unit', role}));
const act = (role, action) => admin({op:'service', role, action});
const files = name => join(ops, 'workbench-everyday-' + name + '.service');
const read = path => JSON.parse(readFileSync(path));
const boot = readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim();

process.umask(0o077);
need(process.argv.length === 4 && /^(?:codex-once|qwen-once|codex-with-local-fallback|qwen-with-codex-fallback|bug-flight-paths)$/.test(process.argv[2]),
  'Expected one sealed case and the exact approved package reference');
const [, , choice, approvalReference] = process.argv;
const windowPath = join(evidence, 'window.json');
const state = lstatSync(windowPath);
need(realpathSync(windowPath) === windowPath && state.isFile() && state.uid === process.getuid() &&
  !(state.mode & 0o077) && state.size < 4096, 'Window receipt is not private');
const window = read(windowPath);
const meta = read(join(ops,'release.json'));
const seal = sha(join(ops, 'bundle.sha256'));
const opened = Date.parse(window.opened_at);
const policy = read(join(ops, 'browser-policy.json'));
const campaign = policy.campaign;
const outcome = policy.outcome;
need(!outcome || (!campaign && outcome.mode === 'owner-outcome-v1' && outcome.cumulative_caps === false &&
  outcome.preserve_accounting === true && outcome.authority === 'STANDING_OWNER_DIRECTION_AUTONOMOUS_DELIVERY.md' &&
  Number.isInteger(window.outcome?.attempt) && window.outcome.attempt >= 1 &&
  Number.isInteger(window.outcome?.revision) && window.outcome.revision >= 0 &&
  Array.isArray(window.outcome?.prior_attempts)), 'Standing outcome authority is missing or changed');
const duration = outcome ? 150 : campaign ? campaign.minutes : policy.windowMinutes === 600 ? 600 : 480;
const campaignOpened = outcome ? Date.parse(window.outcome.opened_at) : campaign ? Date.parse(window.campaign?.opened_at) : opened;
// This watchdog bounds only this driver operation; it is not an assignment clock.
const cutoff = outcome ? now()+duration*minute : campaignOpened + (duration - (campaign?.restorationMinutes ?? 30))*minute;
need(!campaign || (duration === 960 && campaign.restorationMinutes === 120 &&
  Number.isInteger(window.campaign?.attempt) && window.campaign.attempt >= 1 &&
  window.campaign.attempt <= 8 && Number.isInteger(window.campaign.revision) &&
  window.campaign.revision >= 0 && window.campaign.revision <= 3 &&
  campaignOpened <= opened && Array.isArray(window.campaign.prior_attempts)),
  'Bounded campaign binding is missing or changed');
need(window.approval_reference === approvalReference && /^[a-f0-9]{40}$/.test(approvalReference) &&
  window.commit === meta.commit && window.seal === seal && window.boot === boot &&
  now() >= opened && now() < cutoff, 'Fresh exact installation and live approval is required');
need(['kernel_filters', 'history_preserved', 'lan_tls', 'remote_lan_access'].every(key => window.preflight?.[key] === true),
  'Installed preflight is incomplete');
const index = policy.cases.findIndex(c => c.choice === choice);
need(index >= 0, 'Unknown case');
need(outcome || index !== 0 || now() < opened + 30*minute, 'Installation preflight window expired');
const candidate = policy.cases[index];
const prior = outcome ? [...window.outcome.prior_attempts] : campaign ? [...window.campaign.prior_attempts] : [];
for (const earlier of policy.cases.slice(0, index)) {
  const rows = readFileSync(join(evidence, earlier.choice + '.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  const last = rows.at(-1);
  need(rows[0].event === 'open' && rows[0].approval === approvalReference && rows[0].seal === seal &&
    last.event === 'checked' && last.case === earlier.choice && Array.isArray(last.attempts), 'Prior case not settled');
  prior.push(...last.attempts);
}
need(sha(helper) === helperSha, 'Administrator helper changed');
const grant = JSON.parse(admin({op:'status'}));
need(grant.expires * 1000 > (outcome ? now() : campaignOpened) + duration*minute, 'Administrator grant does not cover rollback window');
const bug = candidate.request.kind === 'issue';
const blockDeadline = Math.min(cutoff, now() + (bug ? 120 : 105)*minute);
const receipt = join(evidence, choice + '.jsonl');
const fd = openSync(receipt, 'wx', 0o600); // An uncertain dispatch consumes this case.
let count = 0;
function record(event, detail) {
  need(++count <= 32, 'Decision evidence budget exhausted');
  const line = JSON.stringify({time:new Date().toISOString(), actor:'owner-delegated acceptance automation',
    case:choice, event, ...detail});
  need(Buffer.byteLength(line) < 4096, 'Decision evidence record too large');
  writeSync(fd, line + '\n');
  fsyncSync(fd);
}

function installed(role, expected = 'active') {
  const info = unit(role);
  const path = join('/etc/systemd/system', 'workbench-everyday-' + role + '.service');
  const file = lstatSync(path);
  need(info.ActiveState === expected && info.User === 'rich' && info.Group === 'rich' &&
    info.NeedDaemonReload === 'no' && info.FragmentPath === path &&
    file.isFile() && file.uid === 0 && !(file.mode & 0o022) && sha(path) === sha(files(role)),
    'Installed Workbench role differs from sealed service');
  return info;
}

function checkControls(isolated, connectorOff) {
  const worker = installed('worker');
  installed('codex', connectorOff ? 'inactive' : 'active');
  const api = installed('api');
  const lan = installed('lan');
  const net = readlinkSync('/proc/' + worker.MainPID + '/ns/net');
  const different = net !== readlinkSync('/proc/self/ns/net');
  need(different === isolated && Boolean(worker.DropInPaths) === isolated &&
    (isolated ? worker.DropInPaths === '/etc/systemd/system/workbench-everyday-worker.service.d/20-acceptance-private-network.conf' : true),
    'Worker network namespace differs');
  if (connectorOff) {
    try { lstatSync(connector); throw new Error('Stopped connector socket still exists'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  } else need(lstatSync(connector).isSocket(), 'Codex connector socket absent');
  need(!api.DropInPaths && !lan.DropInPaths, 'Unexpected listener override');
  const model = unit('model');
  need(model.ActiveState === 'active' && Number(model.MainPID) > 0, 'Original model is not active');
  need(sha(meta.normal_model_key) === window.normal_model_key_sha256, 'Normal Qwen access key was not restored');
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
try {
  record('open', {approval:approvalReference, seal, commit:meta.commit, priorCases:index});
  // The operator verifies the entire sealed inventory and mutable policy bindings before launch.
  host('/usr/bin/sha256sum', ['--status','--check',join(ops,'bundle.sha256')], undefined, 12000);
  checkControls(false, false);
  let controlsAltered = false;
  const control = async phase => {
    if (!candidate.reason) return;
    need(now() < blockDeadline, 'Case deadline expired before control change');
    const isolated = candidate.reason === 'local_unreachable';
    const inducing = phase === 'planning';
    if (inducing || controlsAltered) {
      // The installed helper requires both ingress services closed before stopping a worker or connector.
      act('lan','stop'); act('api','stop');
      if (isolated) {
        act('worker','stop');
        admin({op:'drop-in', action:inducing ? 'on' : 'off'});
        act('worker','start');
      } else {
        act('codex', inducing ? 'stop' : 'start');
      }
      act('api','start'); act('lan','start');
      controlsAltered = inducing;
      const end = Date.now() + 10000;
      for (;;) {
        try {
          const local = JSON.parse(host('/usr/bin/curl', ['--noproxy','*','--max-time','2','-fsS',origin + '/api/local-work']));
          if (local.ready && local.codex_available === !(inducing && !isolated)) break;
        } catch { /* Readiness is bounded and no model endpoint is called. */ }
        need(Date.now() < end, 'Worker did not become ready after controlled service transition');
        await wait(250);
      }
      checkControls(inducing && isolated, inducing && !isolated);
      record('service-control', {phase, isolated:inducing && isolated, connectorOff:inducing && !isolated});
    }
  };
  const reviewPath = join(evidence, 'bug-plan-review.json');
  function readBugReview(intake, plan) {
    const info = lstatSync(reviewPath);
    need(realpathSync(reviewPath) === reviewPath && info.isFile() && info.uid === process.getuid() &&
      !(info.mode & 0o077) && info.size <= 4096, 'Bug plan review is not private');
    const receipt = read(reviewPath);
    need(receipt.decision === 'approve' && receipt.approved === true,
      'Independent review did not explicitly approve this plan');
    need(receipt.approval_reference === approvalReference && receipt.seal === seal &&
      receipt.intake === intake && receipt.plan_binding === plan.binding &&
      receipt.expectation_digest === plan.task_contract.bug_evidence.review_digest &&
      Array.isArray(receipt.observations) && receipt.observations.length === candidate.criteria.length &&
      receipt.observations.every((row, i) => row.criterion === candidate.criteria[i] &&
        Number.isSafeInteger(row.index) && row.index >= 0 && row.index < plan.task_contract.bug_evidence.plan.reproducer.observations.length &&
        typeof row.reason === 'string' && row.reason.trim().length >= 12 && row.reason.length <= 500) &&
      new Set(receipt.observations.map(row => row.index)).size === candidate.criteria.length,
      'Independent review does not bind the three acceptance observations');
    return sha(reviewPath);
  }
  let reviewedSha;
  const callPolicy = (verb, action, value) => {
    let result;
    try {
      result = host(join(release, '.venv/bin/python'), [join(release, 'scripts/acceptance_policy.py'), verb,
      '--policy', join(ops,'browser-policy.json'), '--database', join(stage,'artifacts/workbench.sqlite3'),
      '--preparation', join(ops,'preparation.json')],
    JSON.stringify({...value, action, choice, opened:outcome ? window.outcome.opened_at : campaign ? window.campaign.opened_at : window.opened_at,
      prior, ...(outcome ? {outcome:window.outcome} : campaign ? {campaign:window.campaign} : {})}), 15000);
    } catch (error) {
      let reason = 'Acceptance policy input or state is unavailable.';
      try {
        const held = JSON.parse(String(error.stderr).trim());
        if (held.held === true && typeof held.reason === 'string' && held.reason.length <= 200 &&
          /^[A-Za-z0-9 .,:_()\/-]+$/.test(held.reason)) reason = held.reason;
      } catch { /* No raw child error, command body or private source reaches the receipt. */ }
      record('policy-held', {action, reason});
      throw new Error('acceptance policy check held');
    }
    return JSON.parse(result);
  };
  browser = await chromium.launch({executablePath:join(stage,'browser/chrome-headless-shell-linux64/chrome-headless-shell'),
    args:['--disable-dev-shm-usage','--disable-background-networking','--no-proxy-server']});
  const context = await browser.newContext({serviceWorkers:'block'});
  await runJourney(context, origin, policy, candidate, {
    blockDeadline, now, preparationMs:38*minute, reviewMs:10*minute,
    // The app's bug investigation shares ten minutes across source reads and
    // corrections. Allow receipt/worker polling to settle after that bound.
    planningMs:bug ? 11*minute : 2.5*minute,
    planningReserveMs:bug ? 11*minute : 2.5*minute, record, controlsReady:control,
    async reviewBug({intake, plan}) {
      callPolicy('admit', 'plan', {intake, proposal:plan});
      record('awaiting-independent-plan-review', {intake, proposal:plan.id, binding:plan.binding});
      const end = Math.min(blockDeadline - 98*minute, now() + 10*minute);
      for (;;) {
        try {
          reviewedSha = readBugReview(intake, plan);
          record('independent-plan-review', {intake, proposal:plan.id, binding:plan.binding, reviewSha:reviewedSha});
          break;
        } catch (error) {
          if (error.code !== 'ENOENT') throw error;
        }
        need(now() < end, 'Independent bug plan review did not settle within the approved window');
        await wait(500);
      }
    },
    async beforeAction(action, data) {
      need(now() < blockDeadline && sha(join(ops,'bundle.sha256')) === seal, 'Case window or seal changed');
      for (const role of ['api','lan','worker','codex']) installed(role,
        action === 'submit' && role === 'codex' && candidate.reason === 'unavailable' ? 'inactive' : 'active');
      // The approved controller checks effective cgroups and LAN TLS separately.
      if (bug && action !== 'submit')
        need(reviewedSha === readBugReview(data.intake, data.proposal), 'Independent bug plan review changed');
      callPolicy('admit', action, data);
    },
    async finish(data) {
      checkControls(false, false);
      return callPolicy('closeout','finish',data);
    },
  });
} catch {
  process.stderr.write('Acceptance held. Inspect the case receipt and settle owned work before rollback.\n');
  process.exitCode = 2;
} finally {
  await browser?.close();
  closeSync(fd);
}
