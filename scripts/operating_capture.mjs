// Read-only browser inspection of the isolated built review; no API writes.
import { chromium } from '@playwright/test';
import { mkdirSync,writeFileSync } from 'node:fs';
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({baseURL:'http://127.0.0.1:8787',timezoneId:'America/Denver',reducedMotion:'reduce'});
const page=await context.newPage();
await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
mkdirSync('artifacts/operating',{recursive:true});
const evidence={screenshots:[],geometry:[],observed:null};
async function ready(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function capture(name){await ready();const path=`artifacts/operating/${name}.png`;await page.screenshot({path,fullPage:true});evidence.screenshots.push(path);}
try {
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:width===390?844:1000});
    await page.goto('/');await page.getByLabel('Demo scenario').selectOption('mixed');
    await page.getByRole('heading',{name:'Simulated state',exact:true}).waitFor();
    await capture(`work-demo-${width}`);
    await page.goto('/backlog?source=fixture&type=task&state=Waiting+for+clarification');
    await page.locator('.context-row').first().waitFor();
    await page.locator('.filter-panel').evaluate(element=>element.open=true);
    await capture(`backlog-filtered-demo-${width}`);
    await page.goto('/backlog?source=fixture');
    await page.getByRole('button',{name:/(Expand|Collapse) Make guide progress easier to follow/}).waitFor();
    if(await page.getByRole('button',{name:'Expand Make guide progress easier to follow'}).count()) await page.getByRole('button',{name:'Expand Make guide progress easier to follow'}).click();
    await page.getByRole('button',{name:/(Expand|Collapse) Clear, predictable step selection/}).waitFor();
    if(await page.getByRole('button',{name:'Expand Clear, predictable step selection'}).count()) await page.getByRole('button',{name:'Expand Clear, predictable step selection'}).click();
    await page.getByRole('link',{name:/Keep your place in a guide/}).waitFor();
    await capture(`backlog-expanded-demo-${width}`);
    await page.goto('/settings');
    await page.getByText('Last acknowledged',{exact:true}).waitFor();
    await capture(`settings-${width}`);
    await page.goto('/monitoring');await page.getByLabel('Resource source').selectOption('demo');
    await page.locator('[data-chart-ready="true"]').nth(2).waitFor();
    await capture(`monitoring-demo-${width}`);
    await page.getByLabel('Resource source').selectOption('observed');
    await page.getByText('Observed host · pilot collector · fresh',{exact:true}).waitFor();
    await page.locator('[data-chart-ready="true"]').nth(2).waitFor();
    await capture(`monitoring-observed-${width}`);
  }
  evidence.observed=await (await page.request.get('/api/resources?source=observed&hours=168')).json();
  evidence.observed={source:evidence.observed.source,freshness:evidence.observed.freshness,first_at:evidence.observed.first_at,last_at:evidence.observed.last_at,latest:evidence.observed.latest,points:evidence.observed.points.length};
  for(const path of ['/settings','/monitoring']){
    await page.goto(path);
    for(const width of [320,390,768,1024,1440]){
      await page.setViewportSize({width,height:900});
      for(const size of ['100%','200%']){
        await page.evaluate(value=>document.documentElement.style.fontSize=value,size);
        await ready();
        const geometry=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
        evidence.geometry.push({path,size,...geometry});
        if(geometry.scroll>geometry.width)throw new Error(JSON.stringify({path,size,...geometry}));
      }
      await page.evaluate(()=>document.documentElement.style.fontSize='');
    }
  }
} finally {writeFileSync('artifacts/operating/inspection.json',JSON.stringify(evidence,null,2));await browser.close();}
