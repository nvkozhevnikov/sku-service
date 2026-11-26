document.querySelectorAll('.open-dialog').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.dialog).showModal()));
document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
document.querySelectorAll('[data-history-back]').forEach(button=>button.addEventListener('click',()=>history.back()));
document.querySelectorAll('.manual-start').forEach(form=>{
  const button=form.querySelector('.manual-start-button');
  const fields=[...form.querySelectorAll('input[type=number]')];
  const update=()=>{button.disabled=!form.checkValidity()||Number(form.elements.delay_min_seconds.value)>Number(form.elements.delay_max_seconds.value);};
  fields.forEach(input=>input.addEventListener('input',update));update();
  form.addEventListener('submit',()=>{button.disabled=true;button.textContent='Запускаем…';});
});
// Do not prepare a run with a speed the operator has edited but not saved.
document.querySelectorAll('.collection-speed').forEach(form=>{
  const fields=[...form.querySelectorAll('input[type="number"]')];
  const initial=fields.map(input=>Number(input.value));
  const buttons=[...document.querySelectorAll('.collection-prepare button')];
  const blocked=buttons.map(button=>button.disabled);
  const hint=document.createElement('p');
  hint.className='form-hint';hint.setAttribute('role','status');form.append(hint);
  const update=()=>{
    const changed=fields.some((input,index)=>!input.validity.valid||Number(input.value)!==initial[index]);
    buttons.forEach((button,index)=>button.disabled=blocked[index]||changed);
    hint.textContent=changed?'Сначала сохраните изменённый диапазон паузы.':'';
  };
  fields.forEach(input=>input.addEventListener('input',update));
});
