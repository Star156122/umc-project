const test=require('node:test'),assert=require('node:assert/strict');
const {validateMultiRunRequest,createMultiStrategyBacktestService}=require('../src/multiStrategyBacktestService');
const config={stock_universe:['2330'],periods:{backtest:{start:'2024-07-01',end:'2024-12-30'}}};
const body={stock_code:'2330',strategies:['technical']};
test('custom dates are real validated periods, not client-side chart filters',()=>{
 assert.deepEqual(validateMultiRunRequest({...body,start_date:'2024-12-01',end_date:'2024-12-30'},config).period,{start:'2024-12-01',end:'2024-12-30'});
 for(const [start,end] of [['2024-02-30','2024-12-30'],['2024-12-20','2024-12-01'],['2025-07-01','2025-07-31'],['2026-07-01','2026-07-31'],['2024-06-30','2024-07-30']])assert.throws(()=>validateMultiRunRequest({...body,start_date:start,end_date:end},config));
});
test('new report and chart JSX parse',()=>{const fs=require('fs'),path=require('path'),parser=require('@babel/parser');for(const name of ['BacktestTradeChart','ReportText'])parser.parse(fs.readFileSync(path.join(__dirname,'../../components',name+'.js'),'utf8'),{sourceType:'module',plugins:['jsx']});});
test('daily candles aggregate OHLC and match trades without inventing fills',async()=>{
 const fs=require('fs'),vm=require('vm'),path=require('path');
 const source=fs.readFileSync(path.join(__dirname,'../../components/BacktestTradeChart.js'),'utf8');
 const fn=source.slice(source.indexOf('export function tradeChartDocument'),source.indexOf('export default'));
 const m=await import('data:text/javascript;base64,'+Buffer.from(fn).toString('base64'));
 const t=Date.parse('2024-12-02T09:00:00+08:00')/1000;
 const candles=[{time:t,open:10,high:12,low:9,close:11},{time:t+300,open:11,high:14,low:10,close:13}];
 for(const daily of [true,false]){
  let bars,markers;
  const html=m.tradeChartDocument(candles,[{timestamp:t+300,action:'Buy',price:11},{timestamp:t-1,action:'Sell',price:9}],daily);
  const script=html.split('<script>')[1].split('</script>')[0];
  const chart={addCandlestickSeries:()=>({setData:v=>bars=v,setMarkers:v=>markers=v}),timeScale:()=>({fitContent(){}}),subscribeCrosshairMove(){}};
  const library={createChart:()=>chart};
  vm.runInNewContext(script,{window:{LightweightCharts:library},LightweightCharts:library,document:{getElementById:()=>({})}});
  assert.equal(bars.length,daily?1:2);assert.equal(bars[0].high,daily?14:12);assert.equal(bars[0].close,daily?13:11);
  // In daily view both same-day trades map to that day's bar; 5-minute view excludes pre-open times.
  assert.equal(markers.length,daily?2:1);
 }
});
