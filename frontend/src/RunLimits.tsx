import {minutes, type Budget} from './taskProgress';

export function RunLimits({budget}: {budget: Budget}) {
  return <>
    <p>One attempt with the local Qwen model, up to {budget.turns} exchanges and {budget.generated_tokens.toLocaleString()} tokens of model output in total. There is no fallback provider or paid model.</p>
    <p>Up to {minutes(budget.overall_seconds)} from approval, including any wait. Work has up to {minutes(budget.implementation_seconds)} and work plus checks have up to {minutes(budget.complete_run_seconds)}.</p>
    <p>{budget.activate_once
      ? 'If the final readiness checks pass, Workbench temporarily reserves the local model for this run. Other users of that model may wait. Workbench checks that it can stop its own work and restores normal model access afterward.'
      : 'Execution is unavailable here. Saving this decision records your permission only.'}</p>
  </>;
}
