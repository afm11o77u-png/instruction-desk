/** Shared list display-mode helper (指示書デスク + 在庫デスク). */
(function (global) {
  const MODES = [
    { id: 'standard', label: '標準' },
    { id: 'compact', label: 'コンパクト' },
    { id: 'cozy', label: 'ゆったり' },
    { id: 'grid', label: 'グリッド' },
  ];
  const DEFAULT = 'standard';
  const cache = Object.create(null);
  let loadPromise = null;

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    })[c]);
  }

  function normalize(mode) {
    return MODES.some((m) => m.id === mode) ? mode : DEFAULT;
  }

  function get(screenId) {
    return normalize(cache[screenId]);
  }

  function apply(host, screenId) {
    if (!host) return get(screenId);
    const mode = get(screenId);
    host.dataset.displayMode = mode;
    host.classList.remove('dm-standard', 'dm-compact', 'dm-cozy', 'dm-grid');
    host.classList.add('dm-' + mode);
    return mode;
  }

  function controlHtml(screenId) {
    const current = get(screenId);
    return (
      `<label class="display-mode-control">表示` +
      `<select data-display-screen="${esc(screenId)}" aria-label="表示">` +
      MODES.map(
        (m) =>
          `<option value="${m.id}"${m.id === current ? ' selected' : ''}>${m.label}</option>`
      ).join('') +
      `</select></label>`
    );
  }

  function findBar(host, toolbar) {
    if (toolbar) {
      const inToolbar = toolbar.querySelector(':scope > .display-mode-bar');
      if (inToolbar) return inToolbar;
    }
    return host ? host.querySelector(':scope > .display-mode-bar') : null;
  }

  /**
   * Mount the display control.
   * @param {HTMLElement} host list stage that receives dm-* classes
   * @param {string} screenId
   * @param {Function} [onChange]
   * @param {HTMLElement|null} [toolbar] optional filters/toolbar row to keep chrome compact
   */
  function mount(host, screenId, onChange, toolbar) {
    if (!host) return null;
    let bar = findBar(host, toolbar);
    if (bar && toolbar && bar.parentElement !== toolbar) {
      bar.remove();
      bar = null;
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'display-mode-bar';
      if (toolbar) toolbar.appendChild(bar);
      else host.insertBefore(bar, host.firstChild);
    } else if (toolbar && bar.parentElement !== toolbar) {
      toolbar.appendChild(bar);
    }
    bar.classList.toggle('display-mode-bar-inline', !!toolbar);
    bar.innerHTML = controlHtml(screenId);
    const select = bar.querySelector('select[data-display-screen]');
    if (select) {
      select.onchange = () => {
        const next = normalize(select.value);
        cache[screenId] = next;
        apply(host, screenId);
        if (typeof onChange === 'function') onChange(next, screenId);
      };
    }
    apply(host, screenId);
    return bar;
  }

  async function loadAll(fetcher) {
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      try {
        const data = await fetcher();
        const modes = (data && data.modes) || {};
        for (const [key, value] of Object.entries(modes)) {
          cache[key] = normalize(value);
        }
      } catch (_) {
        /* keep defaults; must not block business ops */
      }
    })();
    try {
      await loadPromise;
    } finally {
      loadPromise = null;
    }
  }

  async function set(screenId, mode, saver) {
    const next = normalize(mode);
    cache[screenId] = next;
    if (typeof saver === 'function') {
      try {
        await saver(screenId, next);
      } catch (_) {
        /* UI keeps the chosen mode even if save fails */
      }
    }
    return next;
  }

  function resetCache() {
    for (const key of Object.keys(cache)) delete cache[key];
  }

  global.DisplayMode = {
    MODES,
    DEFAULT,
    esc,
    normalize,
    get,
    set,
    apply,
    controlHtml,
    mount,
    loadAll,
    resetCache,
  };
})(typeof window !== 'undefined' ? window : globalThis);
