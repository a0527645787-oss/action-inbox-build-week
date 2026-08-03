document.querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => {
  const target = document.getElementById(button.dataset.jump);
  if (target) { target.scrollIntoView({behavior: 'smooth', block: 'center'}); target.classList.add('pulse'); }
}));
document.querySelectorAll('form').forEach(form => form.addEventListener('submit', () => {
  const button = form.querySelector('button');
  if (button) { button.disabled = true; button.textContent = 'Working…'; }
}));
document.querySelectorAll('[data-copy-target]').forEach(button => button.addEventListener('click', async () => {
  const target = document.getElementById(button.dataset.copyTarget);
  if (!target) return;
  await navigator.clipboard.writeText(target.value);
  button.textContent = 'Copied';
}));
document.querySelectorAll('[data-execution-status-url]').forEach(panel => {
  const statusUrl = panel.dataset.executionStatusUrl;
  const statusNode = document.getElementById('execution-status');
  if (!statusUrl || !statusNode) return;
  const initialStatus = statusNode.textContent.trim().replaceAll(' ', '_');
  if (['succeeded', 'failed', 'cancelled'].includes(initialStatus)) return;
  const timer = setInterval(async () => {
    try {
      const response = await fetch(statusUrl, {headers: {'Accept': 'application/json'}});
      if (!response.ok) return;
      const execution = await response.json();
      statusNode.textContent = execution.status.replaceAll('_', ' ');
      if (['succeeded', 'failed', 'cancelled'].includes(execution.status)) {
        clearInterval(timer);
        window.location.reload();
      }
    } catch (_) {
      // A transient browser/network failure does not alter durable worker state.
    }
  }, 2000);
});
