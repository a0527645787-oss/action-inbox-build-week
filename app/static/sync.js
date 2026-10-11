/* Shared Inbox/Gmail controller. One request at a time; all provider details stay private. */
(function () {
  function mount(panel, api = {}) {
    const fetcher = api.fetch || window.fetch.bind(window);
    const later = api.later || ((fn, ms) => window.setTimeout(fn, ms));
    const form = panel.querySelector('[data-sync-form]');
    if (!form) return;
    const button = form.querySelector('button');
    const message = panel.querySelector('[data-sync-message]');
    const tasks = panel.querySelector('[data-sync-tasks]');
    const reconnect = panel.querySelector('[data-sync-reconnect]');
    let busy = panel.dataset.active === 'true', failures = 0, polling = false;
    const loading = () => { button.disabled = true; button.textContent = 'Checking new emails…'; panel.setAttribute('aria-busy', 'true'); };
    const failed = () => {
      busy = false; button.disabled = false; button.textContent = 'Try again';
      panel.setAttribute('aria-busy', 'false');
      message.textContent = 'We couldn’t check progress. Please try again; an existing check will safely continue.';
    };
    const render = job => {
      if (typeof job.active !== 'boolean' || typeof job.message !== 'string') throw new Error('invalid response');
      busy = job.active;
      message.textContent = job.message;
      button.disabled = busy;
      button.textContent = busy ? 'Checking new emails…' : job.failed ? 'Try again' : 'Check for new emails';
      panel.setAttribute('aria-busy', String(busy));
      tasks.hidden = !job.tasks_created;
      reconnect.hidden = !job.reconnect;
      if (!busy && job.imported > 0) {
        // Refresh only the inbox list, preserving the live result and page position.
        if (api.refresh) api.refresh();
        else fetcher('/inbox', {headers: {'Accept': 'text/html'}}).then(r => r.ok ? r.text() : '').then(html => {
          if (!html) return;
          const list = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-email-list]');
          const current = document.querySelector('[data-email-list]');
          if (list && current) current.replaceWith(list);
        }).catch(() => {});
      }
    };
    const poll = async () => {
      if (polling || !busy) return;
      polling = true;
      try {
        const response = await fetcher(panel.dataset.statusUrl, {headers: {'Accept': 'application/json'}});
        if (!response.ok) throw new Error('unavailable');
        render(await response.json()); failures = 0;
      } catch (_) {
        failures += 1;
        if (failures >= 3) failed();
        else message.textContent = 'Still checking. Reconnecting to your progress…';
      } finally {
        polling = false;
        if (busy) later(poll, failures ? 4000 : 1500);
      }
    };
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      busy = true; failures = 0; loading(); message.textContent = 'Checking new emails…';
      try {
        const response = await fetcher(form.action, {method: 'POST', headers: {'Accept': 'application/json'}, body: new FormData(form)});
        if (!response.ok) throw new Error('unavailable');
        const job = await response.json();
        if (!/^\/gmail\/sync\/\d+$/.test(job.status_url || '')) throw new Error('invalid status');
        panel.dataset.statusUrl = job.status_url;
        render(job); if (busy) later(poll, 500);
      } catch (_) { failed(); }
    });
    if (busy) { loading(); later(poll, 500); }
    return {poll, render};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {mount};
  else document.querySelectorAll('[data-sync-panel]').forEach(panel => mount(panel));
})();
