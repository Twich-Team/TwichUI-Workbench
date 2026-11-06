/* Test-only browser interaction for the offline supervisor rehearsal. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {join} = require('node:path');
const [origin, evidence, mode, identity, expected] = process.argv.slice(2);

const task = JSON.parse(fs.readFileSync(join(evidence,'rehearsal-task.json')));
async function ready(file, timeout=25000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (fs.existsSync(join(evidence,file))) return;
    await new Promise(resolve => setTimeout(resolve, 75));
  }
  throw Error(file + ' did not appear');
}

(async () => {
  const browser = await chromium.launch({args:['--disable-dev-shm-usage','--disable-background-networking']});
  process.once('SIGTERM', async () => {
    await browser.close();
    process.exit(143);
  });
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const page=await context.newPage();
    page.setDefaultTimeout(12000);
    if (mode==='approve') {
      await ready('owner-step-scope.json');
      const step = JSON.parse(fs.readFileSync(join(evidence,'owner-step-scope.json')));
      await page.goto(step.url);
      await page.getByText('Expected result 1',{exact:true}).waitFor();
      await page.screenshot({path:join(evidence,'proposal-1440.png')});
      await page.getByLabel('I checked the test examples above. The operation, inputs, expected results and wrong results match the behavior I asked for.').check();
      const scopeSaved = page.waitForResponse(r=>r.url().endsWith('/decisions')&&r.request().method()==='POST');
      await page.getByRole('button',{name:'Approve plan',exact:true}).click();
      assert.equal((await scopeSaved).status(),200);
      await ready('owner-step-budget.json', task.scenario.fixture === 'loaded-client' ? 30*60*1000 : 25000);
      await page.reload();
      const offer=page.getByRole('region',{name:'Certified task run approval'});
      assert.equal(await offer.getByRole('radio',{name:'Local implementation only'}).isChecked(),true);
      await offer.screenshot({path:join(evidence,'offer-1440.png')});
      await offer.getByRole('checkbox',{name:/I approve this exact task.*for local implementation/}).check();
      const budgetSaved=page.waitForResponse(r=>r.url().endsWith('/run-approval')&&r.request().method()==='POST');
      await offer.getByRole('button',{name:'Approve local implementation',exact:true}).dblclick();
      assert.equal((await budgetSaved).status(),200);
    } else {
      const url=origin+'/intake/'+identity;
      for (const width of [1440,390]) {
        await page.setViewportSize({width,height:1000});
        await page.goto(url);
        await page.getByRole('heading',{name:task.intake.title,exact:true,level:1}).waitFor();
        const view=await page.request.get(origin+'/api/intake/'+identity).then(r=>r.json());
        assert.equal(view.original.description,task.intake.description);
        if (mode==='checked') {
          await page.getByRole('link',{name:'Review changes and checks'}).click();
          await page.getByRole('heading',{name:'Test changes checked'}).waitFor();
          assert.equal(await page.locator('.check-summary').getByText('Passed',{exact:true}).count(),4);
        } else if (mode==='failure') {
          await page.getByText(expected,{exact:true}).waitFor();
          await page.getByText('Your original request and any example are saved.',{exact:false}).waitFor();
          assert.equal(await page.getByText('Waiting for information',{exact:true}).count(),0);
          assert.equal(await page.getByRole('button',{name:'Send information'}).count(),0);
          assert.equal(await page.getByRole('button',{name:'Approve plan',exact:true}).count(),0);
          if (task.scenario.fixture === 'loaded-client') {
            await page.getByText(view.jobs[0].message,{exact:true}).waitFor();
            assert.match(view.jobs[0].message,/verified addon operation|one core addon source/);
            assert.equal(await page.getByRole('button',{name:'Retry planning'}).count(),0);
            assert.equal(await page.getByText('Example 1:',{exact:false}).isVisible(),true);
          } else {
            await page.getByText('Original request, planning history and evidence',{exact:true}).click();
            assert.equal(await page.getByRole('heading',{name:'Submitted text example'}).isVisible(),true);
          }
        } else {
          await page.getByText('Ready for your review',{exact:true}).waitFor();
          if (mode === 'proposal') {
            await page.getByText('Expected result 1',{exact:true}).waitFor();
            assert.equal(await page.getByText('TwichUI.MapViewMath.Fade',{exact:false}).first().isVisible(),true);
            assert.equal(await page.getByRole('button',{name:'Approve plan',exact:true}).isVisible(),true);
            assert.equal(await page.getByRole('button',{name:'Approve local implementation',exact:true}).count(),0);
          }
        }
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await page.screenshot({path:join(evidence,`review-${width}.png`),fullPage:mode==='failure'});
      }
    }
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
