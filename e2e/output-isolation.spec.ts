import {test,expect} from '@playwright/test';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {join,relative} from 'node:path';
import {tmpdir} from 'node:os';
import {createRunOutput,withinRun,artifactPath} from './output';
import {capture} from './helpers';

test('ordinary browser output preserves prior evidence and allocates separate runs',async({page},info)=>{
  const fixture=mkdtempSync(join(tmpdir(),'wb-output-control-'));
  try {
    const old=join(fixture,'artifacts/m5-implementation/screenshots');mkdirSync(old,{recursive:true});
    const protectedFile=join(old,'candidate.png');writeFileSync(protectedFile,'historical original');
    const one=createRunOutput(fixture),two=createRunOutput(fixture);
    expect(one).not.toBe(two);
    for(const root of [one,two]) writeFileSync(withinRun(root,'m5-implementation/screenshots/candidate.png'),'new controlled output');
    expect(readFileSync(protectedFile,'utf8')).toBe('historical original');
    expect(()=>withinRun(one,'../../m5-implementation/overwrite')).toThrow();
    expect(()=>withinRun(one,protectedFile)).toThrow();
    const run=process.env.WORKBENCH_E2E_RUN!;
    expect(relative(process.cwd(),run)).toMatch(/^artifacts\/runs\/browser-/);
    expect(info.project.outputDir).toContain('/artifacts/runs/browser-');
    await page.goto('/');
    await capture(page,info,'output-isolation-control',{keepInCompact:true});
    expect(readFileSync(artifactPath('screenshots/output-isolation-control.png')).subarray(0,8)).toEqual(Buffer.from([137,80,78,71,13,10,26,10]));
  } finally {rmSync(fixture,{recursive:true,force:true});}
});
