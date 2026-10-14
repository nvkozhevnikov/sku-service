/* Presentation and read-only worker status. Collection is submitted by the form. */
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
  const worker = document.getElementById('mon-worker-state');
  if (worker && worker.dataset.poll === 'yes') {
    const timer = setInterval(async () => {
      try {
        const response = await fetch('/api/competitor-monitoring/run', { credentials: 'same-origin' });
        if (!response.ok) return;
        const state = await response.json(), run = state.active;
        if (run) worker.textContent = `Сбор: ${run.status} · Run ${run.run_id || (run.status === 'STARTING' ? 'ожидает номера' : 'не создан')} · ${run.origin || ''} · ${run.started_at || ''}`;
        if (state.run_health === 'ORPHANED' || state.run_health === 'UNKNOWN') {
          worker.textContent += ' · Состояние процесса требует проверки'; clearInterval(timer);
        } else if (!state.running && run && !['STARTING', 'RUNNING'].includes(run.status)) {
          clearInterval(timer); window.location.reload();
        }
      } catch (_) { /* A disconnected client does not control worker lifetime. */ }
    }, 2000);
  }
})();
