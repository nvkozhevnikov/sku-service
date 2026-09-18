document.querySelectorAll('.open-dialog').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.dialog).showModal()));
document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
