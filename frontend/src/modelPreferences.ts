import type {components} from './api.generated';
type Policy=components['schemas']['ExecutionPolicy'];
export function savedTaskRoute(policy:Policy){
  const primary=policy.preferred;
  if(primary!=='local'&&primary!=='codex')return null;
  const configured=(backend:'local'|'codex')=>policy.providers?.[backend]?.enabled&&Boolean(policy.providers[backend].model);
  if(!configured(primary))return null;
  const direction=primary==='local'?'qwen-to-codex':'codex-to-qwen';
  const other=primary==='local'?'codex':'local';
  const permitted=policy.test_fallback==null?policy.fallback?.includes(other):policy.test_fallback===direction||policy.test_fallback==='both';
  const condition=policy.fallback_on?.includes('unavailable')||(primary==='codex'&&policy.fallback_on?.includes('capacity'));
  const switchAllowed=policy.substitution==='configured_fallback_only'&&permitted&&condition&&configured(other);
  return primary==='local'?(switchAllowed?'qwen-with-codex-fallback':'qwen-once'):(switchAllowed?'codex-with-local-fallback':'codex-once');
}

export function savedTaskAI(policy:Policy):components['schemas']['TaskAISettings']|null{
 const route=savedTaskRoute(policy);if(!route)return null;
 const primary=policy.preferred as 'local'|'codex',other=primary==='local'?'codex':'local';
 const choice=(provider:'local'|'codex')=>({provider,model:policy.providers![provider].model||'',effort:policy.providers![provider].effort});
 return {primary:choice(primary),fallback:route.includes('fallback')?choice(other):null};
}
export function taskAIRoute(settings:components['schemas']['TaskAISettings']):components['schemas']['IntakeInput']['local_planning']{
 return settings.primary.provider==='local'?(settings.fallback?'qwen-with-codex-fallback':'qwen-once'):(settings.fallback?'codex-with-local-fallback':'codex-once');
}
