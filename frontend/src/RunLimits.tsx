import {minutes, type Budget} from './taskProgress';

export function RunLimits({budget, codex, repair}: {budget: Budget; codex?: boolean; repair?:boolean}) {
  if(codex&&repair)return <p>One bounded Codex repair with up to {budget.turns} model turns and up to {minutes(budget.implementation_seconds)} for implementation. Completed turns may be followed by corrections using check feedback. Uncertain generation holds; no provider switch. Included-allowance checks remain snapshots.</p>;
  if (codex) return <p>One Codex turn, up to two minutes, within the approved run and verification window. Workbench checks included allowance and holds if additional credits are available or credit status is unknown. This run has no provider switch or automatic retry.</p>;
  return <p>{budget.activate_once
    ? <>One attempt with the local model. Allow up to {minutes(budget.overall_seconds)}, including any wait. No paid model use. Other local model users may have to wait.</>
    : <>Execution is unavailable here. This records permission for one local attempt with a {minutes(budget.overall_seconds)} limit, including any wait.</>}</p>;
}

export function ExactRunLimits({budget, codex, repair}: {budget: Budget; codex?: boolean; repair?:boolean}) {
  if(codex&&repair)return <p>At most {budget.turns} turns, 393,216 bytes per request packet and five minutes per provider call, within the implementation watchdog. Workbench retains each completed turn’s reported usage. There is no enforceable provider token ceiling or per-turn included-allowance lock. No API-key billing or purchases.</p>;
  if (codex) return <p>One turn, 24 KB per request packet, two minutes per provider call, and one exact edit. Workbench checks reported tokens after generation. Allowance checks are snapshots. The provider offers no per-turn billing switch or enforceable token ceiling.</p>;
  return <p>At most {budget.turns} exchanges, {budget.generated_tokens.toLocaleString()} generated tokens total and {budget.response_tokens.toLocaleString()} per response. Up to {minutes(budget.implementation_seconds)} for the work and {minutes(budget.complete_run_seconds)} through checks. No fallback.</p>;
}
