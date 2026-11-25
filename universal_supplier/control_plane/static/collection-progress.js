const statuses = {
  queued: "В очереди",
  running: "Выполняется",
  succeeded: "Завершено",
  partial: "Завершено частично",
  failed: "Остановлено с ошибкой",
  stale_failed: "Прервано",
  lease_expired: "Нет связи с исполнителем",
  cancelled: "Остановлено",
  skipped_already_queued: "Уже в очереди",
};
const percentText = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });

for (const root of document.querySelectorAll("[data-collection-progress]")) {
  const phase = root.querySelector("[data-phase]");
  async function refresh() {
    try {
      const response = await fetch(root.dataset.collectionProgress, { cache: "no-store" });
      if ([401, 403, 404].includes(response.status) || response.redirected) return;
      if (!response.ok) throw new Error("progress unavailable");
      const progress = await response.json();
      const status = progress.job_status || progress.run_status || progress.status;
      const active = status === "queued" || status === "running";
      if (!active && root.closest("[data-rendered-running]")) {
        window.location.reload();
        return;
      }
      phase.textContent = (active && progress.phase_label_ru) || statuses[status] || "Ожидается";
      const durable = progress.durable_progress === true;
      const count = (durable ? progress.succeeded : progress.processed) ?? 0;
      const total = progress.total;
      const percent = durable
        ? (total > 0 ? Math.min(100, 100 * count / total) : null)
        : (progress.percent ?? null);
      const bar = root.querySelector("[data-bar]");
      if (percent === null) bar.removeAttribute("value");
      else bar.value = percent;
      root.querySelector("[data-count]").textContent = (durable ? "Сохранено " : "Обработано ") + count
        + (total == null ? "" : " из " + total)
        + (percent === null ? "" : " · " + percentText.format(percent) + "%");
      for (const [selector, count, label] of [
        ["[data-deferred]", progress.deferred, "Отложено из-за сети"],
        ["[data-matching-conflicts]", progress.matching_conflicts, "Конфликты сопоставления"],
        ["[data-other-card-issues]", progress.other_card_issues, "Прочие проблемы с карточками"],
      ]) {
        const line = root.querySelector(selector);
        line.hidden = !count;
        line.textContent = label + ": " + (count ?? 0);
      }
      const link = root.querySelector("[data-run-link]");
      if (link && Number.isInteger(progress.run_id)) {
        link.href = "/runs/" + progress.run_id;
        link.hidden = false;
      }
      if (active) setTimeout(refresh, 3000);
    } catch {
      phase.textContent = "Не удалось обновить прогресс. Проверяем снова.";
      setTimeout(refresh, 10000);
    }
  }
  refresh();
}
