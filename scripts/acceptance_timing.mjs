/** Per-operation watchdogs; these are not cumulative delivery limits. */
export function caseTiming(bug) {
  const timing = Object.freeze({
    caseMinutes:bug ? 140 : 105,
    planningMinutes:bug ? 21 : 2.5,
    reviewMinutes:bug ? 10 : 0,
    preparationMinutes:38,
    runMinutes:60,
    cleanupMinutes:10,
    operationMinutes:150,
  });
  const admission = timing.planningMinutes + timing.reviewMinutes +
    timing.preparationMinutes + timing.runMinutes;
  if (admission + 1 > timing.caseMinutes ||
      timing.caseMinutes + timing.cleanupMinutes > timing.operationMinutes)
    throw new Error('Acceptance phases do not fit the operation watchdog');
  return timing;
}
