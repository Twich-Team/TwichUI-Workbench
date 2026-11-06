import {minutes, type Budget} from './taskProgress';

export function RunLimits({budget}: {budget: Budget}) {
  return <p>{budget.activate_once
    ? <>One attempt with the local model. Allow up to {minutes(budget.overall_seconds)}, including any wait. No paid model use. Other local model users may have to wait.</>
    : <>Execution is unavailable here. This records permission for one local attempt with a {minutes(budget.overall_seconds)} limit, including any wait.</>}</p>;
}

export function ExactRunLimits({budget}: {budget: Budget}) {
  return <p>At most {budget.turns} exchanges, {budget.generated_tokens.toLocaleString()} generated tokens total and {budget.response_tokens.toLocaleString()} per response. Up to {minutes(budget.implementation_seconds)} for the work and {minutes(budget.complete_run_seconds)} through checks. No fallback.</p>;
}
