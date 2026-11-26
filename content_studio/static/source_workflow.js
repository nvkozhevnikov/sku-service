'use strict';
let sourceImportInfo=null,sourceReadTimer=null,sourceReadPromise=null,sourceReadEpoch=0;
let publicationLinkMode='auto',lastAutoTitle='',presetManual=false,sourceUndo=null;
let sourceBrowserAvailable=false;
const sourceBrowserRequests=new Map();
function sourceBrowserMessage(action,url){
  const id=crypto.randomUUID();
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{sourceBrowserRequests.delete(id);action==='hello'?resolve(null):reject(Error('Браузерный импорт не ответил. Проверьте модуль и доступ к карточке.'));},action==='hello'?650:40000);
    sourceBrowserRequests.set(id,{resolve,reject,timer});
    window.postMessage({application:'STERBRUST_STUDIO_SOURCE',id,action,url},location.origin);
  });
}
window.addEventListener('message',event=>{
  if(event.source!==window||event.origin!==location.origin||event.data?.application!=='STERBRUST_SOURCE_BROWSER')return;
  const request=sourceBrowserRequests.get(event.data.id);if(!request)return;
  clearTimeout(request.timer);sourceBrowserRequests.delete(event.data.id);
  if(event.data.ready){sourceBrowserAvailable=true;request.resolve(true);return;}
  const result=event.data.result;
  if(result?.error)request.reject(Error(result.error));else request.resolve(result);
});
async function detectSourceBrowser(){
  sourceBrowserAvailable=Boolean(await sourceBrowserMessage('hello'));
  $('compose-browser-status').textContent=sourceBrowserAvailable?'Браузерный импорт подключён.':'Если сайт блокирует чтение ссылки, подключите браузерный импорт один раз.';
  return sourceBrowserAvailable;
}
const onlySourceLink=value=>/^(?:https?:\/\/|(?:www\.)?sterbrust\.(?:com|ru)\/)[^\s<>]+$/i.test(value.trim());

function ourPublicationLink(value){
  value=canonicalSiteLink(value);
  try {const u=new URL(value);if(u.protocol==='https:'&&['sterbrust.com','www.sterbrust.com','sterbrust.ru','www.sterbrust.ru'].includes(u.hostname)&&!u.username&&!u.password&&!u.port)return u.href;}catch{}
  return '';
}
function paintAutoPublicationLink(){
  if(publicationLinkMode==='manual'){
    $('compose-link-origin').textContent=$('compose-publication-url').value.trim()?'Ссылка изменена вручную. Автоматически не заменяем.':'Ссылка убрана. Автоматически не возвращаем.';return;
  }
  const value=ourPublicationLink($('compose-url').value);
  $('compose-publication-url').value=value;
  $('compose-link-origin').textContent=value?'Подставлена ссылка источника. Можно изменить или убрать.':'Ссылка нашей страницы подставится автоматически.';
}
function sourceStatus(text,error=false){$('compose-source-status').textContent=text;$('compose-source-status').dataset.error=String(error);}
function sourceBusy(busy){$('compose-load-page').disabled=busy;$('compose-submit').disabled=busy||composeBusy;}
function initSourceWorkflow(existing){
  clearTimeout(sourceReadTimer);sourceReadEpoch++;sourceReadPromise=null;sourceImportInfo=null;sourceUndo=null;
  publicationLinkMode=existing?'manual':'auto';lastAutoTitle='';presetManual=false;
  $('compose-source-undo').hidden=true;sourceBusy(false);sourceStatus('Вставьте текст или одну ссылку Sterbrust. Подготовим выбранные версии; обложку добавляете отдельно.');
  paintAutoPublicationLink();
  detectSourceBrowser();
}
function candidateSourceURL(){
  const text=$('compose-source').value.trim();
  return onlySourceLink(text)?text:$('compose-url').value.trim();
}
async function loadSourcePage(explicit=false){
  const url=candidateSourceURL();if(!url){sourceStatus('Вставьте ссылку на страницу товара или заполните ссылку источника.',true);return;}
  if(sourceReadPromise)return sourceReadPromise;
  clearTimeout(sourceReadTimer);const epoch=++sourceReadEpoch;
  const before={source:$('compose-source').value,title:$('compose-title').value,url:$('compose-url').value,
    publication:$('compose-publication-url').value,linkMode:publicationLinkMode,info:sourceImportInfo};
  sourceBusy(true);sourceStatus('Читаем страницу Sterbrust. Описание и характеристики появятся здесь…');
  sourceReadPromise=(async()=>{
    try {
      let result;
      if(sourceBrowserAvailable){
        const prepared=await api('/api/sources/validate','POST',{url});
        const page=await sourceBrowserMessage('read',prepared.url);
        result=await api('/api/sources/from-browser','POST',page);
      } else result=await api('/api/sources/read','POST',{url});
      if(epoch!==sourceReadEpoch)return;
      sourceUndo=before;$('compose-source-undo').hidden=false;
      $('compose-source').value=result.source;$('compose-url').value=result.url;
      if(!$('compose-title').value.trim()||$('compose-title').value===lastAutoTitle){$('compose-title').value=result.title;lastAutoTitle=result.title;}
      if(!presetManual&&result.source_kind==='product_page')$('compose-preset').value='product';
      sourceImportInfo=result;paintAutoPublicationLink();refreshComposerConsent();await analyseComposeTags();
      sourceStatus('Страница прочитана. Название, описание и характеристики загружены. Проверьте исходник перед подготовкой.');
    } catch(error) {
      if(epoch!==sourceReadEpoch)return;
      sourceStatus(error.message,true);if(!sourceBrowserAvailable)$('compose-browser-help').open=true;throw error;
    } finally {
      if(epoch===sourceReadEpoch){sourceReadPromise=null;sourceBusy(false);}
    }
  })();
  return sourceReadPromise;
}
async function ensureSourceReady(){
  clearTimeout(sourceReadTimer);
  if(sourceReadPromise)await sourceReadPromise;
  if(onlySourceLink($('compose-source').value)||(!$('compose-source').value.trim()&&$('compose-url').value.trim()))await loadSourcePage();
}
function sourceImportOptions(){return {publication_link_mode:publicationLinkMode,...(sourceImportInfo?{source_import_id:sourceImportInfo.id,source_kind:$('compose-source').value===sourceImportInfo.source?sourceImportInfo.source_kind:'manual'}:{})};}
$('compose-source').addEventListener('input',()=>{
  clearTimeout(sourceReadTimer);
  if(sourceReadPromise){sourceReadEpoch++;sourceReadPromise=null;sourceBusy(false);}
  if(onlySourceLink($('compose-source').value)){
    $('compose-url').value=$('compose-source').value.trim();paintAutoPublicationLink();
    sourceReadTimer=setTimeout(()=>loadSourcePage().catch(()=>{}),650);
  } else if(sourceImportInfo)sourceStatus('Исходник изменён вручную. Ссылка и ваша редакция сохранены.');
  else sourceStatus($('compose-source').value.trim()?'Текст вставлен. Можно подготовить выбранные версии.':'Вставьте текст или ссылку на страницу.');
});
$('compose-url').addEventListener('input',()=>{
  if(sourceReadPromise){sourceReadEpoch++;sourceReadPromise=null;sourceBusy(false);}
  paintAutoPublicationLink();clearTimeout(sourceReadTimer);
  if(!$('compose-source').value.trim()&&onlySourceLink($('compose-url').value))sourceReadTimer=setTimeout(()=>loadSourcePage().catch(()=>{}),650);
});
$('compose-publication-url').addEventListener('input',()=>{publicationLinkMode='manual';paintAutoPublicationLink();});
$('compose-link-from-source').onclick=()=>{publicationLinkMode='auto';paintAutoPublicationLink();};
$('compose-preset').addEventListener('change',()=>{presetManual=true;});
$('compose-load-page').onclick=()=>loadSourcePage(true).catch(()=>{});
$('compose-source-undo').onclick=()=>{
  if(!sourceUndo)return;const previous=sourceUndo;sourceReadEpoch++;sourceUndo=null;sourceImportInfo=previous.info;
  $('compose-source').value=previous.source;$('compose-title').value=previous.title;$('compose-url').value=previous.url;
  $('compose-publication-url').value=previous.publication;publicationLinkMode=previous.linkMode;
  $('compose-source-undo').hidden=true;sourceBusy(false);paintAutoPublicationLink();refreshComposerConsent();queueTags();sourceStatus('Предыдущий исходник восстановлен.');
};
$('compose-browser-help').addEventListener('toggle',async()=>{
  if(!$('compose-browser-help').open)return;
  try {const info=await api('/api/sources/browser-module-info');$('compose-browser-folder').value=info.folder;}catch{}
});
$('compose-copy-browser-folder').onclick=guarded(async()=>{
  await navigator.clipboard.writeText($('compose-browser-folder').value);sourceStatus('Путь к папке модуля скопирован. Установка выполняется вами в браузере.');
});
