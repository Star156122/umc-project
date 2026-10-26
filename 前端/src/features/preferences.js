import {apiRequest} from '../config/api';
export const DEFAULT_PREFERENCES={stock:'2330',strategies:['technical','rf','xgboost','gru'],showCharts:true,expandDetails:false,completionNotice:true,browserNotice:false};
const listeners=new Set();
export function subscribePreferences(fn){listeners.add(fn);return()=>listeners.delete(fn);}
export async function loadPreferences(){return (await apiRequest('/api/account')).settings;}
export async function savePreferences(value){const data=await apiRequest('/api/account/settings',{method:'PUT',body:JSON.stringify(value)});listeners.forEach(fn=>fn(data.settings));return data.settings;}
export async function notifyBacktestCompleted(stock){
 try{const p=await loadPreferences();if(!p.completionNotice)return false;
 if(p.browserNotice&&typeof window!=='undefined'&&window.Notification?.permission==='granted'){try{new window.Notification('回測分析完成',{body:stock+' 的歷史回測已完成，請回到網站查看結果。'});}catch(_){}}
 return true;}catch(_){return false;}
}
