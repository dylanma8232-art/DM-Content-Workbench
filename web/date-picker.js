/* Progressive enhancement: the original date input remains the form/API source. */
(() => {
  'use strict';
  const boot = () => {
    const enhanced = new WeakMap();
    const svg = (path) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
    const calendar = svg('<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M16 3v4M8 3v4M3 11h18M8 15h.01M12 15h.01M16 15h.01M8 18h.01M12 18h.01"/>');
    const arrow = (right) => svg(`<path d="${right ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'}"/>`);
    const date = (year, month, day = 1) => {
      const result = new Date(0);
      result.setHours(12, 0, 0, 0);
      result.setFullYear(year, month, day);
      return result;
    };
    const key = (value) => `${String(value.getFullYear()).padStart(4, '0')}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
    const parse = (value) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
      const [y, m, d] = value.split('-').map(Number);
      const result = date(y, m - 1, d);
      return key(result) === value && y > 0 ? result : null;
    };
    const today = () => { const now = new Date(); return date(now.getFullYear(), now.getMonth(), now.getDate()); };
    let active = null;
    let focused = today();
    let view = date(focused.getFullYear(), focused.getMonth());
    const panel = document.createElement('div');
    panel.id = 'dm-date-picker';
    panel.className = 'dm-date-picker dmDatePopover';
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-label', '选择日期');
    if ('showPopover' in panel) panel.setAttribute('popover', 'manual');
    panel.innerHTML = `<div class="dm-date-header"><button type="button" class="dm-date-prev" aria-label="上个月">${arrow(false)}</button><div class="dm-date-selects"><select data-native-select aria-label="年份"></select><select data-native-select aria-label="月份"></select></div><button type="button" class="dm-date-next" aria-label="下个月">${arrow(true)}</button></div><div class="dm-date-caption" aria-live="polite" aria-atomic="true"></div><div class="dm-date-grid" role="grid" aria-label="日期"></div><div class="dm-date-footer"><button type="button" class="dm-date-today">今天</button><button type="button" class="dm-date-close">关闭</button></div>`;
    document.body.append(panel);
    const yearSelect = panel.querySelector('[aria-label="年份"]');
    const monthSelect = panel.querySelector('[aria-label="月份"]');
    const grid = panel.querySelector('.dm-date-grid');
    const previous = panel.querySelector('.dm-date-prev');
    const next = panel.querySelector('.dm-date-next');
    const todayButton = panel.querySelector('.dm-date-today');
    const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
    const rangeStart = (input) => document.getElementById(input.dataset.rangeStart || '');
    const refreshDateRange = (input) => {
      const trigger = enhanced.get(input);
      const label = trigger?.querySelector('.dm-date-range-label');
      if (!label) return;
      const start = rangeStart(input)?.value;
      const end = input.value;
      label.textContent = start && end && start !== end ? `${start} 至 ${end}` : end || start || '选择日期范围';
      trigger.setAttribute('aria-label', end || start ? `选择日期范围，${label.textContent}` : label.textContent);
    };
    window.refreshDateRange = refreshDateRange;
    const bounds = () => [parse(active.input.min) || date(1, 0, 1), parse(active.input.max) || date(9999, 11, 31)];
    const allowed = (value) => { const [min, max] = bounds(); return value >= min && value <= max; };
    const clamp = (value) => { const [min, max] = bounds(); return value < min ? min : value > max ? max : value; };
    const position = () => {
      if (!active) return;
      const rect = active.trigger.getBoundingClientRect();
      const viewport = window.visualViewport;
      const left = viewport ? viewport.offsetLeft : 0;
      const top = viewport ? viewport.offsetTop : 0;
      const width = viewport ? viewport.width : document.documentElement.clientWidth;
      const height = viewport ? viewport.height : window.innerHeight;
      panel.style.maxHeight = `${Math.max(80, height - 16)}px`;
      const box = panel.getBoundingClientRect();
      panel.style.left = `${Math.max(left + 8, Math.min(rect.right - box.width, left + width - box.width - 8))}px`;
      panel.style.top = `${Math.max(top + 8, Math.min(rect.bottom + 8 + box.height <= top + height - 8 ? rect.bottom + 8 : rect.top - box.height - 8, top + height - box.height - 8))}px`;
    };
    const close = (restore = false) => {
      if (!active) return;
      const old = active;
      active = null;
      old.trigger.setAttribute('aria-expanded', 'false');
      if (panel.hidePopover && panel.matches(':popover-open')) panel.hidePopover();
      panel.hidden = true;
      if (restore && old.trigger.isConnected) old.trigger.focus();
    };
    const render = (focusDay = false) => {
      if (!active) return;
      const [min, max] = bounds();
      const y = view.getFullYear(), m = view.getMonth();
      yearSelect.replaceChildren();
      // Keep an ample useful range, expanding to any explicit form constraints.
      const firstYear = active.input.min ? min.getFullYear() : Math.max(1, Math.min(y, today().getFullYear()) - 100);
      const lastYear = active.input.max ? max.getFullYear() : Math.min(9999, Math.max(y, today().getFullYear()) + 100);
      for (let year = firstYear; year <= lastYear; year++) yearSelect.add(new Option(`${year}年`, String(year)));
      yearSelect.value = String(y);
      monthSelect.replaceChildren();
      for (let month = 0; month < 12; month++) {
        const option = new Option(`${month + 1}月`, String(month));
        option.disabled = date(y, month + 1, 0) < min || date(y, month) > max;
        monthSelect.add(option);
      }
      monthSelect.value = String(m);
      previous.disabled = date(y, m, 0) < min;
      next.disabled = date(y, m + 1) > max;
      todayButton.disabled = !allowed(today());
      const { input, start, anchor } = active;
      const first = anchor || start?.value || input.value;
      const last = anchor || input.value || first;
      const prompt = start ? (anchor ? '，请选择第二个日期' : '，请选择第一个日期') : '';
      panel.querySelector('.dm-date-caption').textContent = `${y}年${m + 1}月${prompt}`;
      grid.replaceChildren();
      const heading = document.createElement('div');
      heading.className = 'dm-date-week';
      heading.setAttribute('role', 'row');
      weekdays.forEach((day) => {
        const cell = document.createElement('span');
        cell.setAttribute('role', 'columnheader');
        cell.textContent = day;
        heading.append(cell);
      });
      grid.append(heading);
      const offset = (date(y, m).getDay() + 6) % 7;
      const days = date(y, m + 1, 0).getDate();
      for (let i = 0; i < Math.ceil((offset + days) / 7) * 7; i++) {
        if (i % 7 === 0) {
          const row = document.createElement('div');
          row.className = 'dm-date-week';
          row.setAttribute('role', 'row');
          grid.append(row);
        }
        const day = i - offset + 1;
        const cell = document.createElement(day < 1 || day > days ? 'span' : 'button');
        cell.setAttribute('role', 'gridcell');
        if (cell.tagName === 'BUTTON') {
          const value = date(y, m, day), iso = key(value);
          cell.type = 'button';
          cell.dataset.date = iso;
          cell.textContent = String(day);
          cell.setAttribute('aria-label', `${y}年${m + 1}月${day}日`);
          cell.setAttribute('aria-selected', String(iso >= first && iso <= last));
          if (iso === key(today())) cell.setAttribute('aria-current', 'date');
          cell.disabled = !allowed(value);
          cell.tabIndex = iso === key(focused) && !cell.disabled ? 0 : -1;
        }
        grid.lastElementChild.append(cell);
      }
      position();
      if (focusDay) grid.querySelector('[tabindex="0"]')?.focus();
    };
    const open = (input, trigger) => {
      if (input.disabled || input.readOnly || input.matches(':disabled')) return;
      if (active?.input === input) { close(true); return; }
      close();
      const start = rangeStart(input);
      active = { input, trigger, start, anchor: null };
      grid.setAttribute('aria-multiselectable', String(Boolean(start)));
      panel.setAttribute('aria-label', start ? '选择日期范围' : '选择日期');
      focused = clamp(parse(input.value) || today());
      view = date(focused.getFullYear(), focused.getMonth());
      trigger.setAttribute('aria-expanded', 'true');
      panel.hidden = false;
      if (panel.showPopover) panel.showPopover();
      render(true);
    };
    const choose = (value, singleDay = false) => {
      if (!active || active.input.disabled || active.input.readOnly || active.input.matches(':disabled') || !allowed(value)) return;
      const { input, start, anchor } = active;
      const iso = key(value);
      if (start && !singleDay && !anchor) {
        active.anchor = iso;
        focused = value;
        render(true);
        return;
      }
      if (start) {
        const first = singleDay ? iso : anchor;
        [start.value, input.value] = [first, iso].sort();
        refreshDateRange(input);
        close(true);
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return;
      }
      const changed = input.value !== iso;
      input.value = iso;
      close(true);
      if (changed) {
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    };
    const moveMonth = (amount) => {
      const target = date(view.getFullYear(), view.getMonth() + amount);
      focused = clamp(date(target.getFullYear(), target.getMonth(), Math.min(focused.getDate(), date(target.getFullYear(), target.getMonth() + 1, 0).getDate())));
      view = date(focused.getFullYear(), focused.getMonth());
      render();
    };
    previous.addEventListener('click', () => moveMonth(-1));
    next.addEventListener('click', () => moveMonth(1));
    [yearSelect, monthSelect].forEach((select) => select.addEventListener('change', () => {
      const y = Number(yearSelect.value), m = Number(monthSelect.value);
      focused = clamp(date(y, m, Math.min(focused.getDate(), date(y, m + 1, 0).getDate())));
      view = date(focused.getFullYear(), focused.getMonth());
      render();
    }));
    todayButton.addEventListener('click', () => choose(today(), true));
    panel.querySelector('.dm-date-close').addEventListener('click', () => close(true));
    grid.addEventListener('click', (event) => { const button = event.target.closest('button[data-date]'); if (button) choose(parse(button.dataset.date)); });
    grid.addEventListener('keydown', (event) => {
      const button = event.target.closest('button[data-date]');
      if (!button) return;
      let target = parse(button.dataset.date);
      const shifts = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      if (event.key in shifts) target.setDate(target.getDate() + shifts[event.key]);
      else if (event.key === 'Home') target.setDate(target.getDate() - (target.getDay() + 6) % 7);
      else if (event.key === 'End') target.setDate(target.getDate() + 6 - (target.getDay() + 6) % 7);
      else if (event.key === 'PageUp' || event.key === 'PageDown') {
        moveMonth((event.key === 'PageUp' ? -1 : 1) * (event.shiftKey ? 12 : 1));
        render(true);
        event.preventDefault();
        return;
      } else return;
      event.preventDefault();
      focused = clamp(target);
      view = date(focused.getFullYear(), focused.getMonth());
      render(true);
    });
    const enhance = (input) => {
      if (enhanced.has(input)) return;
      const wrapper = document.createElement('span');
      wrapper.className = 'dm-date-field';
      input.before(wrapper);
      wrapper.append(input);
      input.classList.add('dm-date-input');
      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.className = 'dm-date-trigger';
      trigger.setAttribute('aria-label', '选择日期');
      trigger.setAttribute('title', '选择日期');
      trigger.setAttribute('aria-haspopup', 'dialog');
      trigger.setAttribute('aria-controls', panel.id);
      trigger.setAttribute('aria-expanded', 'false');
      trigger.innerHTML = calendar;
      wrapper.append(trigger);
      enhanced.set(input, trigger);
      if (rangeStart(input)) {
        wrapper.classList.add('dm-date-range');
        input.hidden = true;
        const label = document.createElement('span');
        label.className = 'dm-date-range-label';
        trigger.prepend(label);
        trigger.title = '选择日期范围';
        refreshDateRange(input);
        const syncDisabled = () => { trigger.disabled = input.disabled || input.readOnly; };
        syncDisabled();
        new MutationObserver(syncDisabled).observe(input, { attributes: true, attributeFilter: ['disabled', 'readonly'] });
      }
      input.addEventListener('click', (event) => { event.preventDefault(); open(input, trigger); });
      trigger.addEventListener('click', (event) => { event.preventDefault(); open(input, trigger); });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown' && event.altKey) { event.preventDefault(); open(input, trigger); }
      });
      input.addEventListener('change', () => {
        refreshDateRange(input);
        if (active?.input === input) {
          active.anchor = null;
          focused = clamp(parse(input.value) || today());
          view = date(focused.getFullYear(), focused.getMonth());
          render();
        }
      });
    };
    const scan = (root) => {
      if (!(root instanceof Element) || root.closest('.dm-date-picker')) return;
      if (root.matches('input[type="date"]')) enhance(root);
      root.querySelectorAll('input[type="date"]').forEach(enhance);
    };
    scan(document.body);
    new MutationObserver((records) => {
      records.forEach((record) => record.addedNodes.forEach(scan));
      if (active && !active.input.isConnected) close();
    }).observe(document.body, { childList: true, subtree: true });
    document.addEventListener('pointerdown', (event) => {
      if (active && !panel.contains(event.target) && event.target !== active.trigger && !active.trigger.contains(event.target)) close();
    });
    document.addEventListener('focusin', (event) => {
      if (active && !panel.contains(event.target) && event.target !== active.trigger) close();
    });
    document.addEventListener('keydown', (event) => {
      if (active && event.key === 'Escape') { event.preventDefault(); close(true); }
    });
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position);
    window.visualViewport?.addEventListener('scroll', position);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
