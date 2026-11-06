const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [step, output, origin] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({args:['--disable-dev-shm-usage','--disable-background-networking']});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
    const writing = step === 'configure' ? ['PUT','/api/models/policy'] : ['POST','/api/intake'];
    await context.route('**/*',route=>{
      const req=route.request(), url=new URL(req.url());
      if(url.origin === origin && (['GET','HEAD'].includes(req.method()) ||
         (req.method() === writing[0] && url.pathname === writing[1]))) return route.continue();
      return route.abort();
    });
    const page=await context.newPage();
    page.setDefaultTimeout(10000);
    if(step==='configure'){
      await page.goto(origin+'/models/configure?provider=local');
      await page.locator('.manual-model summary').click();
      await page.getByLabel('Manual model identifier ·').fill('pilot-qwen38-27b');
      await page.getByLabel('Allow this provider in routing').check();
      await page.getByLabel('Configure provider').selectOption('codex');
      await page.getByRole('heading',{name:'Codex',exact:true}).waitFor();
      await page.getByLabel('Allow this provider in routing').uncheck();
      await page.getByLabel('Configure provider').selectOption('claude');
      await page.getByRole('heading',{name:'Claude',exact:true}).waitFor();
      await page.getByLabel('Allow this provider in routing').uncheck();
      if (!await page.locator('.model-routing-settings').evaluate(element=>element.open))
        await page.locator('.model-routing-settings > summary').click();
      await page.getByRole('combobox',{name:'Preferred backend',exact:true}).selectOption('local');
      await page.getByRole('combobox',{name:'Provider fallback',exact:true}).selectOption('deny');
      await page.getByRole('combobox',{name:'When no backend is available',exact:true}).selectOption('block');
      const remove=page.getByRole('button',{name:'Remove Codex',exact:true});
      if(await remove.count()) await remove.click();
      const response=page.waitForResponse(r=>r.url()===origin+writing[1]&&r.request().method()===writing[0]);
      await page.getByRole('button',{name:'Save model preferences',exact:true}).click();
      const result=await response;assert.equal(result.status(),200,await result.text());
      await page.getByText('Configuration saved; not activated.',{exact:true}).waitFor();
      fs.writeFileSync(path.join(output,'browser-policy.json'),JSON.stringify(await result.json(),null,2)+'\n',{flag:'wx',mode:0o600});
    } else {
      assert.equal(step,'submit');
      const {intake_payload: intake} = JSON.parse(fs.readFileSync(path.join(output,'ingress-phase.json'),'utf8'));
      const regression = intake.regression_tests;
      await page.goto(origin+'/intake/new?kind='+intake.kind);
      await page.getByLabel('Title Required').fill(intake.title);
      await page.getByLabel('Description Required').fill(intake.description);
      await page.getByLabel('Add a check for existing behavior').check();
      await page.getByLabel('Expected test results').fill(regression.acceptance.join('\n'));
      await page.getByText('Optional test details',{exact:true}).click();
      await page.getByLabel('Existing test paths').fill(regression.test_paths.join('\n'));
      if (regression.text_example) {
        await page.getByLabel('Add a text example for the first expected result').check();
        await page.getByLabel('Example input',{exact:true}).fill(regression.text_example.input);
        await page.getByLabel('Expected example result',{exact:true}).fill(regression.text_example.expected);
        await page.getByLabel('Incorrect example result to reject',{exact:true}).fill(regression.text_example.incorrect);
      }
      await page.getByLabel('Does this request change the interface?').selectOption(intake.visual_requirement);
      const response=page.waitForResponse(r=>r.url()===origin+writing[1]&&r.request().method()===writing[0]);
      await page.getByRole('button',{name:'Submit request',exact:true}).click();
      const result=await response;assert.equal(result.status(),200,await result.text());
      const saved=await result.json();
      await page.getByText('Request received and saved.',{exact:false}).waitFor();
      assert.equal(new URL(page.url()).pathname,'/intake/'+saved.id);
      fs.writeFileSync(path.join(output,'browser-intake.json'),JSON.stringify({id:saved.id,url:origin+'/intake/'+saved.id},null,2)+'\n',{flag:'wx',mode:0o600});
      await page.screenshot({path:path.join(output,'browser-submitted.png')});
    }
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
