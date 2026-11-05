import {mkdirSync,mkdtempSync} from 'node:fs';
import {resolve,join,relative,isAbsolute,sep} from 'node:path';

// Configuration allocates a fresh directory even if an old run variable is inherited.
// Reference snapshots stay in e2e/*.spec.ts-snapshots; they are never output roots.
export function createRunOutput(projectRoot:string):string {
  const parent=resolve(projectRoot,'artifacts/runs');
  mkdirSync(parent,{recursive:true});
  return mkdtempSync(join(parent,'browser-'));
}
export function withinRun(root:string,name:string):string {
  const target=resolve(root,name);
  const rel=relative(resolve(root),target);
  if(!rel || isAbsolute(name) || rel==='..' || rel.startsWith('..'+sep)) throw new Error('Test output must stay within its new run.');
  mkdirSync(resolve(target,'..'),{recursive:true});
  return target;
}
export function artifactPath(name:string):string {
  const root=process.env.WORKBENCH_E2E_RUN;
  if(!root) throw new Error('Playwright configuration must allocate a fresh evidence run before writing.');
  return withinRun(root,name);
}
