document.querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => {
  const target = document.getElementById(button.dataset.jump);
  if (target) { target.scrollIntoView({behavior: 'smooth', block: 'center'}); target.classList.add('pulse'); }
}));
const resetSubmittedForms = () => document.querySelectorAll('form[data-submitting="true"]').forEach(form => {
  const button = form.querySelector('button[type="submit"], button:not([type])');
  if (button) {
    button.disabled = false;
    button.innerHTML = button.dataset.originalHtml || button.innerHTML;
    delete button.dataset.originalHtml;
  }
  delete form.dataset.submitting;
});

document.querySelectorAll('form:not([data-sync-form])').forEach(form => form.addEventListener('submit', event => {
  if (form.dataset.submitting === 'true') {
    event.preventDefault();
    return;
  }
  const button = form.querySelector('button[type="submit"], button:not([type])');
  form.dataset.submitting = 'true';
  if (button) {
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.textContent = 'Working…';
  }
  window.setTimeout(() => {
    if (document.visibilityState === 'visible') resetSubmittedForms();
  }, 15000);
}));
window.addEventListener('pageshow', resetSubmittedForms);
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
  const initialStatus = statusNode.dataset.status || statusNode.textContent.trim().replaceAll(' ', '_');
  if (['succeeded', 'completed_verified', 'verification_failed', 'failed', 'cancelled'].includes(initialStatus)) return;
  const timer = setInterval(async () => {
    try {
      const response = await fetch(statusUrl, {headers: {'Accept': 'application/json'}});
      if (!response.ok) return;
      const execution = await response.json();
      statusNode.dataset.status = execution.status;
      statusNode.textContent = ({queued: 'Getting ready', running: 'Adding your row…', completed_verified: 'Added and verified', succeeded: 'Done', failed: 'Needs your attention', verification_failed: 'Needs your attention', cancelled: 'Cancelled'})[execution.status] || 'Ready for your review';
      if (['succeeded', 'completed_verified', 'verification_failed', 'failed', 'cancelled'].includes(execution.status)) {
        clearInterval(timer);
        window.location.reload();
      }
    } catch (_) {
      // A transient browser/network failure does not alter durable worker state.
    }
  }, 2000);
});

const tableChoice = document.getElementById('table-choice');
if (tableChoice) tableChoice.addEventListener('change', () => {
  document.querySelectorAll('[data-destination-blocker]').forEach(note => {
    note.hidden = note.dataset.destinationBlocker !== tableChoice.value;
  });
});
