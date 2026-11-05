// Local pinned Apache ECharts 5.6.0. No runtime CDN or replacement chart engine.
export type ChartOption = Record<string, unknown>;
export type Chart = { setOption: (option: ChartOption, settings?: {notMerge?: boolean}) => void; resize: () => void; dispose: () => void; on: (name: string, handler: (event: ZoomEvent) => void) => void; dispatchAction: (action: ChartOption) => void; getOption: () => { dataZoom?: {startValue?: number; endValue?: number}[] } };
type ZoomEvent = {batch?: ZoomEvent[]; startValue?: number; endValue?: number};
declare global { interface Window { echarts?: {version: string; init: (element: HTMLElement, theme?: unknown, options?: ChartOption) => Chart} } }
let pending: Promise<void> | undefined;
export function loadCharts() {
  if (window.echarts?.version === '5.6.0') return Promise.resolve();
  if (!pending) pending = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script'); script.src = '/vendor/echarts/echarts-5.6.0.min.js';
    script.onload = () => window.echarts?.version === '5.6.0' ? resolve() : reject(new Error('Chart library unavailable.'));
    script.onerror = () => { pending = undefined; script.remove(); reject(new Error('Charts unavailable. Recorded values remain in the reading table.')); };
    document.head.append(script);
  });
  return pending;
}
export type WindowRange = {start: number; end: number};
export function resourceOptions(points: {at: number; [key:string]: unknown}[], fields: string[], unit: '%'|'GiB', window: WindowRange, zoom?: WindowRange, gpuUnavailable = !points.some(point => typeof point.gpu === 'number')): ChartOption {
  return { animation: false, backgroundColor: 'transparent', textStyle: {color:'#d9cbb6'},
    color: ['#d2a469','#83b6c4'], grid: {left: 52, right: 24, top: 40, bottom: 80},
    legend: {textStyle:{color:'#d9cbb6'}, top:0, formatter:(name: string) => name === 'GPU' && gpuUnavailable ? 'GPU · unavailable' : name},
    tooltip: {trigger:'axis', triggerOn:'mousemove|click', confine:true, valueFormatter:(value: unknown) => typeof value === 'number' ? `${value.toFixed(1)} ${unit}` : 'Unavailable'},
    xAxis: {type:'time', min: window.start, max: window.end, axisLabel:{color:'#c8b99f', hideOverlap:true}},
    yAxis: {type:'value', name:unit, min:0, ...(unit === '%' ? {max:100} : {}), axisLabel:{color:'#c8b99f'}, splitLine:{lineStyle:{color:'#4c463c'}}},
    dataZoom: [{type:'slider', filterMode:'none', startValue:zoom?.start ?? window.start, endValue:zoom?.end ?? window.end,
      textStyle:{color:'#ddc9a8'}, height:25, bottom:18}, {type:'inside', filterMode:'none', zoomOnMouseWheel:'shift', moveOnMouseWheel:false}],
    series: fields.map(field => ({id:field, name:field === 'cpu' ? 'CPU' : field === 'gpu' ? 'GPU' : field === 'ram_used' ? 'System RAM' : 'VRAM',
      type:'line', showSymbol:false, connectNulls:false, data:points.map(point => [point.at*1000, typeof point[field] === 'number' ? Number(point[field])/(unit === 'GiB' ? 2**30 : 1) : null])})) };
}
