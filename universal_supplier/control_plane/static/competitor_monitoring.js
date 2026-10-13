/* Presentation only. No network requests or data mutations. */
(() => {
  const period = document.querySelector('.mon-filters select[name="period"]');
  const showDates = () => document.querySelectorAll('.mon-custom-date').forEach(label => {
    label.hidden = period.value !== 'custom';
    label.querySelector('input').disabled = label.hidden;
  });
  if (period) { period.addEventListener('change', showDates); showDates(); }
  const status = document.querySelector('[data-promo-status]');
  if (status) status.addEventListener('change', () => document.querySelectorAll('[data-promo-state]').forEach(card => {
    card.hidden = !!status.value && card.dataset.promoState !== status.value;
  }));
  const search = document.querySelector('[data-assortment-search]');
  if (search) search.addEventListener('input', () => document.querySelectorAll('.mon-assortment tbody tr').forEach(row => {
    row.hidden = !row.cells[0].textContent.toLocaleLowerCase('ru').includes(search.value.trim().toLocaleLowerCase('ru'));
  }));
})();
