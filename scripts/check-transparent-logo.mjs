import {chromium} from '@playwright/test';import{writeFileSync}from'node:fs';
const b=await chromium.launch();const p=await b.newPage();const evidence=[];
for(const width of [1440,390]){
 await p.setViewportSize({width,height:900});await p.goto('http://127.0.0.1:8787/');
 const result=await p.locator('.brand-logo').evaluate(async img=>{await img.decode();const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;const ctx=c.getContext('2d');ctx.drawImage(img,0,0);const a=ctx.getImageData(0,0,c.width,c.height).data;let min=255,max=0;for(let i=3;i<a.length;i+=4){min=Math.min(min,a[i]);max=Math.max(max,a[i]);}return{src:img.currentSrc,alphaMin:min,alphaMax:max,fit:getComputedStyle(img).objectFit,background:getComputedStyle(img).backgroundColor,overflow:document.documentElement.scrollWidth>innerWidth};});
 if(!result.src.endsWith('twichui-launcher-transparent-edited.png')||result.alphaMin!==0||result.alphaMax!==255||result.overflow)throw Error(JSON.stringify(result));
 await p.screenshot({path:`artifacts/closeout/transparent-logo-${width}.png`,fullPage:true});evidence.push({width,...result});
}
writeFileSync('artifacts/closeout/transparent-logo-check.json',JSON.stringify(evidence,null,2));await b.close();
