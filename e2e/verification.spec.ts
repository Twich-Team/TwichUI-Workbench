import {artifactPath} from './output';
import {test,expect} from '@playwright/test';
import {fixture,answerAll,overflow} from './helpers';
import {mkdirSync} from 'node:fs';
test.beforeEach(()=>fixture('reset'));
for(const width of [1440,390])test(`review to durable fixture verification at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900});
 await page.goto('/tasks/cozy-step-review/verification');
 await expect(page.getByText('Contract review required',{exact:true})).toBeVisible();
 await page.getByRole('link',{name:'Review candidate'}).click();await answerAll(page);
 await page.getByRole('button',{name:'Save review decisions'}).click();fixture('worker');
 await expect(page.getByRole('dialog').getByText('Applied',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close review'}).click();
 await page.getByRole('link',{name:'Verify fixture candidate'}).click();
 await expect(page.getByRole('button',{name:'Request fixture verification'})).toBeVisible();
 mkdirSync(artifactPath('m5/screenshots'),{recursive:true});
 await page.screenshot({path:artifactPath(`m5/screenshots/ready-${width}.png`),fullPage:true});
 await page.getByRole('button',{name:'Request fixture verification'}).click();
 await expect(page.getByRole('heading',{name:'Request saved · Waiting for worker'})).toBeVisible();
 await page.reload();await expect(page.getByText('Verification: unknown · Attempt: not_run · Publication: held')).toBeVisible();
 await page.screenshot({path:artifactPath(`m5/screenshots/pending-${width}.png`),fullPage:true});
 fixture(width===1440?'verify-fixture':'verify-fixture-fail');
 await expect(page.getByRole('heading',{name:width===1440?'Fixture checks completed':'Verification failed'})).toBeVisible();
 await page.screenshot({path:artifactPath(`m5/screenshots/result-${width}.png`),fullPage:true});await overflow(page);
 if(width===390){await page.evaluate(()=>document.documentElement.style.fontSize='200%');await overflow(page);await page.screenshot({path:artifactPath('m5/screenshots/result-phone-200.png'),fullPage:true});}
});
test('verification response loss recovers the same request without duplicate work',async({page})=>{
 await page.goto('/tasks/cozy-step-review/review');await answerAll(page);await page.getByRole('button',{name:'Save review decisions'}).click();fixture('worker');await expect(page.getByRole('dialog').getByText('Applied',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close review'}).click();await page.getByRole('link',{name:'Verify fixture candidate'}).click();
 await page.route('**/api/tasks/cozy-step-review/verification',async route=>{if(route.request().method()==='POST'){await route.fetch();await route.abort();}else await route.continue();});
 await page.getByRole('button',{name:'Request fixture verification'}).click();
 await expect(page.getByRole('heading',{name:'Request saved · Waiting for worker'})).toBeVisible();
 await page.unroute('**/api/tasks/cozy-step-review/verification');await page.reload();
 expect((await(await page.request.get('/api/tasks/cozy-step-review/verification')).json()).length).toBe(1);
 fixture('verify-fixture');await expect(page.getByRole('heading',{name:'Fixture checks completed'})).toBeVisible();
});

for(const [width,scale] of [[1440,'100%'],[390,'100%'],[390,'200%']] as const)test(`prerequisite action is a primary keyboard link at ${width}px ${scale}`,async({page})=>{
 await page.setViewportSize({width,height:900});
 await page.goto('/tasks/cozy-step-review/verification');
 await page.evaluate(value=>document.documentElement.style.fontSize=value,scale);
 await expect(page.getByText('Contract review required',{exact:true})).toBeVisible();
 const action=page.getByRole('link',{name:'Review candidate',exact:true});
 await expect(action).toHaveAttribute('href','/tasks/cozy-step-review/review');
 await expect(action).toHaveClass(/button-primary/);
 await page.getByRole('link',{name:'← Back to candidate'}).focus();await page.keyboard.press('Tab');
 await expect(action).toBeFocused();
 const focus=await action.evaluate(el=>({outline:getComputedStyle(el).outlineStyle,width:getComputedStyle(el).outlineWidth}));
 expect(focus.outline).not.toBe('none');expect(parseFloat(focus.width)).toBeGreaterThan(0);
 await overflow(page);mkdirSync(artifactPath('m5-action'),{recursive:true});
 await page.screenshot({path:artifactPath(`m5-action/prerequisite-${width}-${scale}.png`),fullPage:true});
 let writes=0;page.on('request',r=>{if(r.method()!=='GET')writes++;});
 await page.keyboard.press('Enter');await expect(page.getByRole('dialog')).toBeVisible();
 expect(writes).toBe(0);
 expect((await(await page.request.get('/api/tasks/cozy-step-review/verification')).json()).length).toBe(0);
});
