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
