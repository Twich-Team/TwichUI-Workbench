import { expect, it } from 'vitest';
import { resourceOptions } from '../src/charts';
import { value } from '../src/Monitoring';

it('keeps CPU percentages distinct from byte-based RAM and leaves gaps null', () => {
  const points=[{at:100,cpu:35,ram_used:8*2**30},{at:115,cpu:null,ram_used:null}];
  const window={start:100000,end:115000};
  const percent=resourceOptions(points,['cpu'],'%',window) as {series:{data:unknown[][];connectNulls:boolean}[];yAxis:{max:number}};
  expect(percent.series[0].data).toEqual([[100000,35],[115000,null]]);
  expect(percent.yAxis.max).toBe(100);
  expect(percent.series[0].connectNulls).toBe(false);
  const memory=resourceOptions(points,['ram_used'],'GiB',window) as {series:{data:unknown[][]}[]};
  expect(memory.series[0].data[0]).toEqual([100000,8]);
  expect(value(null,'%')).toBe('Unavailable');
  expect(value(8*2**30,'GiB')).toBe('8.0 GiB');
  expect(value(16*2**20,'GiB')).toBe('0.016 GiB');
  expect(value(1,'GiB')).toBe('<0.001 GiB');
});
