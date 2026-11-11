# Owner clarification: global and per-task AI configuration

Recorded November 10, 2026 (Mountain time), during the autonomous overnight campaign. This is an explicit product requirement for the existing model-settings milestone. Preserve the campaign's priority order and finish active verification without interrupting or restarting running work.

- Settings must select the preferred provider, its actual supported model and supported reasoning effort. Configure fallback providers with the same model/effort precision.
- Create a task defaults to saved global preferences. An optional **Choose a different AI for this task** control allows provider, model and supported effort overrides. Advanced choices remain optional for ordinary requests. Deliberately selecting a capable Codex model with High effort is a valid use case when the installed integration supports it.
- Persist the task's resolved configuration. Later global changes must not silently alter already-authorized running work.
- Record and display requested provider/model/effort separately from the provider/model/effort actually used, including fallback reasons. Unknown actual values remain unknown.
- Validate choices against supported models and effort levels of the installed integration. Do not invent models, effort support or allowance data.
- Preserve subscription, usage, spending, disclosure, source/scope, independent-check and execution safeguards. Per-task overrides grant no bypass.

The clarification itself needs no immediate implementation, owner response or interruption of the current milestone. Incorporate it into the model-settings work under the existing campaign authority.

Current delivery distinction: the installed simple primary/fallback screen links to existing advanced global model/effort controls. The precise optional per-task model/effort override and its complete requested-versus-actual acceptance remain an explicit open increment; provider-only consent is not completion of this requirement.
