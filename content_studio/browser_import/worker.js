'use strict';
const owned=['sterbrust.com','www.sterbrust.com','sterbrust.ru','www.sterbrust.ru'];
let busy=false;
function validPage(value){
  if(typeof value!=='string'||value.length>3000||/[\s\\\x00-\x1f]|%[01][0-9a-f]|%25|%2e|%2f|%5c|%3f|%23/i.test(value))throw Error('Некорректный адрес страницы.');
  const u=new URL(value);
  if(u.protocol!=='https:'||!owned.includes(u.hostname)||u.username||u.password||u.port||u.search||!/^\/(?:catalog|company\/news|news|articles)\//.test(u.pathname))throw Error('Нужна публичная страница Sterbrust.');
  return u.href;
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  let origin;
  try {origin=new URL(sender.url);} catch {return;}
  if(origin.protocol!=='http:'||origin.hostname!=='127.0.0.1'||origin.pathname!=='/'||!['58206','58207'].includes(origin.port)||!sender.tab||message?.action!=='read-sterbrust-page')return;
  if(busy){respond({error:'Дождитесь завершения предыдущего импорта.'});return;}
  busy=true;
  (async()=>{
    let tab;
    try {
      const url=validPage(message.url);tab=await chrome.tabs.create({url:'about:blank',active:false});
      await new Promise(resolve=>setTimeout(resolve,150));
      await chrome.tabs.update(tab.id,{url});
      let result;
      const end=Date.now()+35000;
      while(Date.now()<end){
        try {
          const entries=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{
            if(location.protocol!=='https:'||!['sterbrust.com','www.sterbrust.com','sterbrust.ru','www.sterbrust.ru'].includes(location.hostname)||location.search||!/^\/(?:catalog|company\/news|news|articles)\//.test(location.pathname))return null;
            const h=document.querySelector('h1');const body=document.querySelector('#desc,[itemprop="description"],.detail_text,article');
            if(!h||!body||body.innerText.trim().length<30)return null;
            const copy=body.cloneNode(true),originals=[body,...body.querySelectorAll('*')],copies=[copy,...copy.querySelectorAll('*')];
            for(let i=copies.length-1;i>=0;i--){
              const node=copies[i],original=originals[i],style=getComputedStyle(original);
              if(/^(SCRIPT|STYLE|FORM|INPUT|TEXTAREA|SELECT|BUTTON|IFRAME|OBJECT|EMBED|NOSCRIPT)$/.test(node.tagName)||original.hidden||original.getAttribute('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden'){
                if(i===0)return null;node.remove();continue;
              }
              for(const attribute of [...node.attributes])if(!['colspan','rowspan'].includes(attribute.name))node.removeAttribute(attribute.name);
            }
            const root=document.createElement('main'),title=document.createElement('h1'),region=document.createElement('div');
            title.textContent=h.innerText.trim();region.id='desc';region.innerHTML=copy.innerHTML;root.append(title,region);
            return {url:location.href,html:root.outerHTML};
          }});
          result=entries[0]?.result;
          if(result)break;
        } catch {}
        await new Promise(resolve=>setTimeout(resolve,500));
      }
      if(!result)throw Error('Карточка не прочитана. Проверьте, что она открывается в этом браузере. Проверки защиты проходите самостоятельно.');
      result.url=validPage(result.url);
      if(typeof result.html!=='string'||result.html.length>1000000)throw Error('Описание слишком большое. Вставьте нужный текст вручную.');
      respond(result);
    } catch(error){respond({error:error.message||'Импорт не завершён.'});}
    finally {if(tab)try{await chrome.tabs.remove(tab.id);}catch{}busy=false;}
  })();
  return true;
});
