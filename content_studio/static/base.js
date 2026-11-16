'use strict';
const studioBase=document.querySelector('meta[name="studio-base"]')?.content||'';
function studioURL(path){
  if(typeof path==='string'&&(path==='/api'||path.startsWith('/api/')||path.startsWith('/static/')))return studioBase+path;
  return path;
}
function studioFetch(path,options){return fetch(studioURL(path),options);}
function applyStudioPermissions(session){
  if(session.mode!=='UNIFIED_RC')return;
  document.body.dataset.integrated='true';
  const admin=session.role==='ADMIN',rights=new Set(session.permissions||[]);
  const controls={
    'content.edit':['new-project','welcome-new','blank-project','save-button','approve-button','template-button','compose-existing','welcome-import','apply-clean','history-open'],
    'content.media':['add-media','compose-add-media'],
    'content.export':['export-top','copy-text'],
    'content.vk.publish':['vk-prepare','vk-confirm-send'],
  };
  for(const [right,ids] of Object.entries(controls))if(!admin&&!rights.has(right))for(const id of ids){const e=document.getElementById(id);if(e){e.disabled=true;e.title='Недостаточно прав';}}
  for(const id of ['vk-connect-desktop','vk-user-setup','ai-add-profile']){const e=document.getElementById(id);if(e&&!admin)e.hidden=true;}
  const desktop=document.getElementById('vk-connect-desktop');if(desktop)desktop.hidden=true;
}
