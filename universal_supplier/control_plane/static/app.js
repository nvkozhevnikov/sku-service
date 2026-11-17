document.querySelectorAll('.open-dialog').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.dialog).showModal()));
document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
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
