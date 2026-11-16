'use strict';
if(location.pathname==='/'&&['58206','58207'].includes(location.port)) {
  window.addEventListener('message',async event=>{
    if(event.source!==window||event.origin!==location.origin)return;
    const data=event.data;
    if(!data||data.application!=='STERBRUST_STUDIO_SOURCE'||typeof data.id!=='string'||!/^[a-z0-9-]{10,80}$/i.test(data.id))return;
    if(data.action==='hello') {
      window.postMessage({application:'STERBRUST_SOURCE_BROWSER',id:data.id,ready:true},location.origin);return;
    }
    if(data.action!=='read'||typeof data.url!=='string')return;
    try {
      const result=await chrome.runtime.sendMessage({action:'read-sterbrust-page',url:data.url});
      window.postMessage({application:'STERBRUST_SOURCE_BROWSER',id:data.id,result},location.origin);
    } catch {
      window.postMessage({application:'STERBRUST_SOURCE_BROWSER',id:data.id,result:{error:'Браузерный импорт не завершён. Откройте карточку и проверьте доступ.'}},location.origin);
    }
  });
}
