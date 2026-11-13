import { test, expect } from '@playwright/test';
import { fixture, capture, overflow } from './helpers';

test.beforeEach(()=>fixture('reset'));

test('observed machine resources show real-source fields, partial GPU and stale service health honestly',async ({page},info)=>{
  const at=Math.floor(Date.now()/1000);
  const latest={at,cpu:28.5,gpu:null,ram_used:20*2**30,ram_total:64*2**30,vram_used:2*2**30,vram_total:16*2**30,gpu_device:'Controlled AMD',disk_used:130*2**30,disk_total:226*2**30,disk_available:86*2**30,swap_used:2*2**30,swap_total:8*2**30};
  let stale=false;
  await page.route('**/api/resources?source=observed*',async route=>{
    const response=await route.fetch();const previous=await response.json();
    await route.fulfill({response,json:{...previous,label:'Controlled observed collector',freshness:'fresh',latest,points:[latest],age_s:0,first_at:at,last_at:at,status_at:at-(stale?90:0),status_freshness:stale?'stale':'fresh',metric_notes:{gpu:'This GPU driver does not provide a readable utilization counter.'},model:{alias:'controlled-qwen',file:'controlled-model.gguf',bytes:14*2**30,mapped:true,api:'ready'},services:['api','worker','connector','private_access','model'].map((name,index)=>({name,state:'active',pid:index+100,restarts:0}))}});
  });
  await page.goto('/monitoring');
  await expect(page.getByRole('heading',{name:'Machine resources',exact:true})).toBeVisible();
  await expect(page.locator('.resource-card').filter({hasText:'CPU utilization'})).toContainText('28.5 %');
  await expect(page.locator('.resource-card').filter({hasText:'Application disk'})).toContainText('86.0 GiB available');
  await expect(page.locator('.resource-card').filter({hasText:'Swap'})).toContainText('2.0 GiB / 8.0 GiB');
  await expect(page.locator('.resource-card').filter({hasText:'GPU utilization'})).toContainText('Not reported');
  await expect(page.locator('.resource-card').filter({hasText:'VRAM'})).toContainText('2.0 GiB / 16.0 GiB');
  await expect(page.locator('.resource-card').filter({hasText:'Active model'})).toContainText('controlled-qwen');
  await expect(page.locator('.resource-card').filter({hasText:'Service health'})).toContainText('5 of 5 running');
  await page.getByText('Model and process details',{exact:true}).click();
  await expect(page.getByText(/Model file: controlled-model.gguf/)).toBeVisible();
  await capture(page,info,'machine-resources-observed-desktop');
  await page.setViewportSize({width:390,height:844});await overflow(page);
  await capture(page,info,'machine-resources-observed-phone');
  await page.evaluate(()=>document.documentElement.style.fontSize='200%');await overflow(page);
  const cards=await page.locator('.resource-card').evaluateAll(elements=>elements.map(e=>e.getBoundingClientRect()));
  expect(cards.every(r=>r.width>=280)).toBe(true);
  expect(new Set(cards.map(r=>r.x)).size).toBe(1);
  await capture(page,info,'machine-resources-observed-phone-enlarged');
  await page.evaluate(()=>document.documentElement.style.fontSize='');
  stale=true;
  await page.getByRole('button',{name:'Refresh display',exact:true}).click();
  await expect(page.getByText('These observations do not confirm current health.',{exact:false})).toBeVisible();
  await expect(page.getByText('Last observed: running',{exact:true})).toHaveCount(5);
  await expect(page.getByText(/Access verified/)).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Last observed: running',{exact:true})).toHaveCount(5);
});

test('display preferences persist, preserve dirty edits, reject conflicts and use exact ownership',async ({page},info)=>{
  await page.goto('/settings');
  await expect(page.getByRole('heading',{name:'Settings',exact:true})).toBeVisible();
  await page.getByText('Display and monitoring preferences',{exact:true}).click();
  await expect(page.getByText('Saved. Preferences apply immediately.',{exact:true})).toBeVisible();
  await page.getByLabel('Default chart time range').selectOption('24');
  await page.getByLabel('Display refresh',{exact:true}).selectOption('15');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Dashboard',exact:true}).click();
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('link',{name:'Settings',exact:true}).click();
  await page.getByText('Display and monitoring preferences',{exact:true}).click();
  await expect(page.getByLabel('Default chart time range')).toHaveValue('24');
  await page.reload();
  await page.getByText('Display and monitoring preferences',{exact:true}).click();
  await expect(page.getByLabel('Display refresh',{exact:true})).toHaveValue('15');
  await page.getByRole('button',{name:'Save display preferences'}).click();
  await expect(page.getByText('Display preferences saved and applied.')).toBeVisible();
  await page.getByLabel('Default chart time range').selectOption('168');
  await page.evaluate(async()=>{
    const {csrf_token}=await (await fetch('/api/session')).json();
    const previous=await (await fetch('/api/display-settings')).json();
    await fetch('/api/display-settings',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf_token},body:JSON.stringify({...previous,resource_hours:1,refresh_seconds:60})});
  });
  await page.getByRole('button',{name:'Save display preferences'}).click();
  await expect(page.getByText(/Display settings changed/)).toBeVisible();
  await expect(page.getByLabel('Default chart time range')).toHaveValue('168');
  await page.getByRole('button',{name:'Discard edits and reload'}).click();
  await expect(page.getByLabel('Default chart time range')).toHaveValue('1');
  await expect(page.getByLabel('Display refresh',{exact:true})).toHaveValue('60');
  await capture(page,info,'operating-settings-desktop');
  await page.setViewportSize({width:390,height:844}); await overflow(page);
  await capture(page,info,'operating-settings-phone');
  await page.evaluate(()=>document.documentElement.style.fontSize='200%'); await overflow(page);
});

test('real ECharts demo updates preserve zoom and legend while observed remains unavailable',async ({page},info)=>{
  await page.goto('/monitoring');
  await expect(page.getByText('Resource source unavailable',{exact:true})).toBeVisible();
  await page.getByLabel('Resource source').selectOption('demo');
  await expect(page.getByText('Deterministic demo · simulated host · demo')).toBeVisible();
  const chart=page.getByRole('img',{name:'CPU / GPU utilization',exact:true});
  await expect(chart).toHaveAttribute('data-chart-ready','true');
  await expect(page.locator('.resource-card').filter({hasText:'GPU utilization'})).toContainText('%');
  const initial=await chart.evaluate(element=>{
    const library=(window as any).echarts;
    const instance=library.getInstanceByDom(element);
    const option=instance.getOption();
    const start=option.xAxis[0].min+10*60*1000, end=option.xAxis[0].min+30*60*1000;
    instance.dispatchAction({type:'dataZoom',startValue:start,endValue:end});
    instance.dispatchAction({type:'legendUnSelect',name:'GPU'});
    return {start,end};
  });
  await page.route('**/api/resources?source=demo*',async route=>{
    const response=await route.fetch(); const data=await response.json();
    data.start+=15; data.end+=15; data.latest={...data.latest,at:data.latest.at+15,cpu:27.5};
    data.points.push(data.latest);
    await route.fulfill({response,json:data});
  });
  await Promise.all([page.waitForResponse(response=>response.url().includes('/api/resources?source=demo')),page.getByRole('button',{name:'Refresh display',exact:true}).click()]);
  await expect.poll(()=>chart.evaluate(el=>(window as any).echarts.getInstanceByDom(el).getOption().series[0].data.at(-1)[1])).toBe(27.5);
  await expect.poll(()=>chart.evaluate(el=>{const opt=(window as any).echarts.getInstanceByDom(el).getOption();return {start:opt.dataZoom[0].startValue,end:opt.dataZoom[0].endValue};})).toEqual(initial);
  expect(await chart.evaluate(el=>(window as any).echarts.getInstanceByDom(el).getOption().legend[0].selected.GPU)).toBe(false);
  await page.getByText('Read values · keyboard and touch',{exact:true}).first().click();
  await expect(page.getByRole('columnheader',{name:'CPU (%)',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Older readings',exact:true}).first().click();
  await page.getByRole('button',{name:'Newer readings',exact:true}).first().click();
  await capture(page,info,'operating-monitoring-demo-desktop');
  await page.setViewportSize({width:390,height:844});await overflow(page);
  await capture(page,info,'operating-monitoring-demo-phone');
  await page.evaluate(()=>document.documentElement.style.fontSize='200%');await overflow(page);
  await page.evaluate(()=>document.documentElement.style.fontSize='');
  await page.getByLabel('Resource source').selectOption('observed');
  await expect(page.getByText('Resource source unavailable',{exact:true})).toBeVisible();
  await expect(page.locator('.resource-card').filter({hasText:'GPU utilization'})).toContainText('Unavailable');
});

test('monitoring handles loading, failed refresh, missing GPU and empty history',async ({page},info)=>{
  let release=()=>{};
  const wait=new Promise<void>(resolve=>release=resolve);
  await page.route('**/api/resources*',async route=>{await wait;await route.continue();});
  await page.goto('/monitoring');
  await expect(page.getByText('Loading resource observations…')).toBeVisible();release();
  await expect(page.getByText('Resource source unavailable',{exact:true})).toBeVisible();
  await page.unroute('**/api/resources*');
  await page.route('**/api/resources*',route=>route.abort());
  await page.getByRole('button',{name:'Refresh display',exact:true}).click();
  await expect(page.getByText(/Refresh failed. These are the last loaded readings/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Resource observations couldn’t be loaded/)).toBeVisible();
  await capture(page,info,'operating-monitoring-failed');
});

test('partial GPU demo keeps CPU, RAM and VRAM available',async ({page},info)=>{
  await page.route('**/api/resources?source=demo*',async route=>{
    const response=await route.fetch();const body=await response.json();
    body.latest.gpu=null;
    body.points.forEach((point:{gpu:number|null})=>point.gpu=null);
    await route.fulfill({response,json:body});
  });
  await page.goto('/monitoring');
  await page.getByLabel('Resource source').selectOption('demo');
  await expect(page.getByText('Deterministic demo · simulated host · demo')).toBeVisible();
  await expect(page.locator('.resource-card').filter({hasText:'GPU utilization'})).toContainText('Unavailable');
  await expect(page.locator('.resource-card').filter({hasText:'CPU utilization'})).toContainText('%');
  await expect(page.locator('.resource-card').filter({hasText:'System RAM'})).toContainText('GiB');
  await expect(page.locator('.resource-card').filter({hasText:'VRAM'})).toContainText('GiB');
  await capture(page,info,'operating-monitoring-partial-demo');
  await page.getByRole('button',{name:'Zoom in',exact:true}).first().focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button',{name:'Zoom in',exact:true}).first()).toBeFocused();
});

test('empty demo history stays empty and does not manufacture readings',async ({page})=>{
  await page.route('**/api/resources?source=demo*',async route=>{
    const response=await route.fetch();const data=await response.json();
    data.points=[];data.latest=null;data.first_at=null;data.last_at=null;
    await route.fulfill({response,json:data});
  });
  await page.goto('/monitoring');
  await page.getByLabel('Resource source').selectOption('demo');
  await expect(page.getByRole('heading',{name:'No readings in this range'})).toBeVisible();
  await expect(page.getByRole('img',{name:'CPU / GPU utilization',exact:true})).toHaveCount(0);
  await expect(page.locator('.resource-card').filter({hasText:'CPU utilization'})).toContainText('Unavailable');
});
