'use strict';

function composerPreservesNews() {
  const mode=$('compose-news-mode').value;
  return selectedChannels().includes('news') && (mode==='preserve' ||
    (mode==='auto' && /<\/?[a-zA-Z][^>]*>/.test($('compose-source').value)));
}
$('compose-news-mode').onchange=refreshComposerConsent;
$('compose-source').addEventListener('input',refreshComposerConsent);

const paintBeforeTemplate=paint;
paint=function(){
  paintBeforeTemplate();
  $('open-preview-window').href=studioURL('/api/projects/'+project.id+'/preview-window/'+channel);
};
$('open-preview-window').onclick=async event=>{
  if(sequence===savedSequence)return;
  event.preventDefault();
  const link=event.currentTarget;
  try { await flush();link.click(); }
  catch(error){message(error.message,'error');}
};

$('mark-generated-photos').onclick=guarded(async()=>{
  await flush();
  for(const image of project.media){
    image.origin=image.origin||'ИИ-генерация (указано оператором)';
    image.rights=image.rights||'Сгенерировано для STERBRUST, добавлено оператором';
  }
  project=await api('/api/projects/'+project.id,'PUT',{project,expected_revision:project.revision});
  paint();message('Происхождение сгенерированных изображений сохранено. Ручной ввод прав не требуется.');
});
