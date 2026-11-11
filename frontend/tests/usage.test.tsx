import {expect,it} from 'vitest';
import type {components} from '../src/api.generated';
import {usageOptions} from '../src/Usage';

it('chart gaps stay unknown while a proven zero and reported partial subtotal remain distinct',()=>{
 const data={days:7,start:'2026-11-05T00:00:00Z',timeline:[
  {day:'2026-11-05',backend:'local',model:'reported',operations:1,reported_tokens:0,unreported_operations:0},
  {day:'2026-11-06',backend:'local',model:'reported',operations:1,reported_tokens:null,unreported_operations:1},
  {day:'2026-11-07',backend:'local',model:'reported',operations:2,reported_tokens:17,unreported_operations:1},
  {day:'2026-11-07',backend:'codex',model:'other',operations:1,reported_tokens:90,unreported_operations:0},
 ]} as components['schemas']['UsageOverview'];
 const options=usageOptions(data,'local');
 const series=options.series as {data:(number|null)[]}[];
 expect(series).toHaveLength(1);
 expect(series[0].data).toEqual([0,null,17,null,null,null,null]);
 expect((options.tooltip as {renderMode:string}).renderMode).toBe('richText');
 expect((options.aria as {enabled:boolean}).enabled).toBe(true);
});
