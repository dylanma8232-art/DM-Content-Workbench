/* Progressive enhancement: the original select remains the form's source of truth.
 * Call DMSelect.refresh(selectOrContainer) after silent option.selected changes.
 * Assignments to enhanced select.value / selectedIndex also refresh immediately.
 */
(() => {
  'use strict';
  if (window.DMSelect || window.SelectControl) return;
  const controls = new Map();
  let current = null;
  let sequence = 0;
  const icon = (path, className) => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 20 20');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', className);
    const line = document.createElementNS(svg.namespaceURI, 'path');
    line.setAttribute('d', path);
    svg.append(line);
    return svg;
  };
  const supported = select => !select.multiple && select.size <= 1 &&
    !select.closest('[data-native-select], .dmCalendar, .dmDatePopover') &&
    !select.matches(':disabled') && !select.querySelector('optgroup, option:disabled');
  const restoreAttribute = (element, name, value) => {
    if (value === null) element.removeAttribute(name);
    else element.setAttribute(name, value);
  };
  const nameOf = select => {
    if (select.getAttribute('aria-label')) return select.getAttribute('aria-label');
    if (select.labels?.length) return Array.from(select.labels, label => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll('select, .dm-select, button, input, textarea').forEach(node => node.remove());
      return copy.textContent.trim();
    }).join(' ');
    return select.title || '请选择';
  };

  class SelectControl {
    constructor(select) {
      this.select = select;
      this.saved = {tabindex: select.getAttribute('tabindex'), ariaHidden: select.getAttribute('aria-hidden')};
      this.wrapper = document.createElement('span');
      this.wrapper.className = 'dm-select dmSelect';
      this.wrapper.classList.toggle('dm-select--project', select.id === 'project');
      this.button = document.createElement('button');
      this.button.type = 'button';
      this.button.className = 'dm-select__button';
      this.button.setAttribute('role', 'combobox');
      this.button.setAttribute('aria-haspopup', 'listbox');
      this.button.setAttribute('aria-expanded', 'false');
      this.text = document.createElement('span');
      this.text.className = 'dm-select__value';
      this.button.append(this.text, icon('m5 7.5 5 5 5-5', 'dm-select__chevron'));
      this.listId = `dm-select-list-${++sequence}`;
      select.before(this.wrapper);
      this.wrapper.append(select, this.button);
      select.classList.add('dm-select__native');
      select.setAttribute('aria-hidden', 'true');
      select.tabIndex = -1;
      this.onChange = () => this.refresh();
      this.onFocus = () => this.button.focus();
      this.onInvalid = () => {
        this.button.setAttribute('aria-invalid', 'true');
        this.button.focus();
      };
      select.addEventListener('change', this.onChange);
      select.addEventListener('input', this.onChange);
      select.addEventListener('focus', this.onFocus);
      select.addEventListener('invalid', this.onInvalid);
      this.button.addEventListener('click', () => this.list ? this.close() : this.open());
      this.button.addEventListener('keydown', event => this.keydown(event));
      this.descriptors = new Map();
      // Instance accessors preserve native setters and never synthesize change events.
      for (const property of ['value', 'selectedIndex']) {
        if (Object.getOwnPropertyDescriptor(select, property)) continue;
        const native = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, property);
        if (!native?.get || !native?.set) continue;
        try {
          Object.defineProperty(select, property, {
            configurable: true,
            get() { return native.get.call(this); },
            set: value => { native.set.call(select, value); this.refresh(); },
          });
          this.descriptors.set(property, true);
        } catch (_) { /* Explicit refresh remains available on non-extensible selects. */ }
      }
      this.refresh();
    }

    refresh() {
      const select = this.select;
      if (this.wrapper.hidden !== select.hidden) this.wrapper.hidden = select.hidden;
      const text = select.selectedOptions[0]?.label || '请选择';
      if (this.text.textContent !== text) this.text.textContent = text;
      this.button.title = text;
      this.button.tabIndex = this.saved.tabindex === null ? 0 : Number(this.saved.tabindex);
      const labelledby = select.getAttribute('aria-labelledby');
      if (labelledby) {
        this.button.setAttribute('aria-labelledby', labelledby);
        this.button.removeAttribute('aria-label');
      } else {
        this.button.removeAttribute('aria-labelledby');
        this.button.setAttribute('aria-label', nameOf(select));
      }
      for (const attribute of ['aria-describedby', 'aria-invalid', 'aria-required']) {
        restoreAttribute(this.button, attribute, select.getAttribute(attribute));
      }
      if (select.required) this.button.setAttribute('aria-required', 'true');
      if (this.list) {
        this.renderOptions();
        this.position();
      }
    }

    renderOptions() {
      this.list.replaceChildren();
      this.items = Array.from(this.select.options, (option, index) => {
        const item = document.createElement('div');
        item.id = `${this.listId}-${index}`;
        item.className = 'dm-select__option';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.hidden = option.hidden;
        const label = document.createElement('span');
        label.textContent = option.label;
        item.append(label, icon('m4.5 10 3.5 3.5 7.5-7.5', 'dm-select__check'));
        item.addEventListener('pointermove', () => this.activate(index, false));
        item.addEventListener('mousedown', event => event.preventDefault());
        item.addEventListener('click', () => this.commit(index));
        this.list.append(item);
        return item;
      });
      this.active = undefined;
      const initial = this.select.selectedIndex;
      this.activate(this.items[initial] && !this.items[initial].hidden ? initial : this.visibleIndices()[0]);
    }

    visibleIndices() {
      return this.items.map((item, index) => item.hidden ? -1 : index).filter(index => index >= 0);
    }

    activate(index, scroll = true) {
      if (index === undefined || !this.items[index] || this.items[index].hidden) {
        this.button.removeAttribute('aria-activedescendant');
        return;
      }
      this.active = index;
      this.items.forEach((item, i) => item.classList.toggle('is-active', i === index));
      this.button.setAttribute('aria-activedescendant', this.items[index].id);
      if (scroll) this.items[index].scrollIntoView({block: 'nearest'});
    }

    open() {
      if (!supported(this.select) || this.select.hidden) return;
      current?.close();
      this.refresh();
      current = this;
      this.list = document.createElement('div');
      this.list.id = this.listId;
      this.list.className = 'dm-select__list';
      this.list.setAttribute('role', 'listbox');
      this.list.setAttribute('aria-label', nameOf(this.select));
      // A manual popover also keeps body-mounted menus above native modal dialogs.
      if ('showPopover' in this.list) this.list.setAttribute('popover', 'manual');
      document.body.append(this.list);
      if (this.list.showPopover) {
        try { this.list.showPopover(); } catch (_) { this.list.removeAttribute('popover'); }
      }
      this.button.setAttribute('aria-controls', this.listId);
      this.button.setAttribute('aria-expanded', 'true');
      this.renderOptions();
      this.position();
      this.activate(this.active);
    }

    position() {
      if (!this.list) return;
      const rect = this.button.getBoundingClientRect();
      const viewport = window.visualViewport;
      const leftEdge = viewport?.offsetLeft || 0;
      const topEdge = viewport?.offsetTop || 0;
      const width = viewport?.width || document.documentElement.clientWidth;
      const height = viewport?.height || window.innerHeight;
      const gap = 6;
      const margin = 8;
      const below = topEdge + height - rect.bottom - gap - margin;
      const above = rect.top - topEdge - gap - margin;
      const upward = below < 220 && above > below;
      this.list.style.width = `${Math.min(Math.max(rect.width, 200), width - margin * 2)}px`;
      this.list.style.maxHeight = `${Math.max(44, Math.min(360, upward ? above : below))}px`;
      this.list.style.left = `${Math.max(leftEdge + margin, Math.min(rect.left, leftEdge + width - this.list.getBoundingClientRect().width - margin))}px`;
      const listHeight = this.list.getBoundingClientRect().height;
      this.list.style.top = `${Math.max(topEdge + margin, upward ? rect.top - gap - listHeight : Math.min(rect.bottom + gap, topEdge + height - listHeight - margin))}px`;
    }

    close(returnFocus = false) {
      if (!this.list) return;
      this.list.remove();
      this.list = null;
      this.search = '';
      this.button.setAttribute('aria-expanded', 'false');
      this.button.removeAttribute('aria-controls');
      this.button.removeAttribute('aria-activedescendant');
      if (current === this) current = null;
      if (returnFocus && this.button.isConnected) this.button.focus({preventScroll: true});
    }

    commit(index) {
      if (index === undefined || !this.items[index] || this.items[index].hidden) return;
      const changed = this.select.selectedIndex !== index;
      this.select.selectedIndex = index;
      this.close(true);
      if (changed) {
        this.select.dispatchEvent(new Event('input', {bubbles: true}));
        this.select.dispatchEvent(new Event('change', {bubbles: true}));
      }
    }

    keydown(event) {
      if (event.isComposing || event.ctrlKey || event.metaKey) return;
      const key = event.key;
      if (key === 'Tab') { this.close(); return; }
      if (key === 'Escape') {
        if (this.list) { event.preventDefault(); event.stopPropagation(); this.close(true); }
        return;
      }
      if (key === 'Enter' || (key === ' ' && !this.search)) {
        event.preventDefault();
        if (this.list) this.commit(this.active);
        else this.open();
        return;
      }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key)) {
        event.preventDefault();
        const wasOpen = !!this.list;
        if (!wasOpen) this.open();
        if (!this.list) return;
        const indices = this.visibleIndices();
        let position = indices.indexOf(this.active);
        if (key === 'Home') position = 0;
        else if (key === 'End') position = indices.length - 1;
        else if (wasOpen) position = Math.max(0, Math.min(indices.length - 1, position + (key === 'ArrowDown' ? 1 : -1)));
        this.activate(indices[position]);
        return;
      }
      if (key.length === 1 && !event.altKey) {
        event.preventDefault();
        if (!this.list) this.open();
        if (!this.list) return;
        const now = Date.now();
        this.search = now - (this.lastSearch || 0) > 700 ? key : (this.search || '') + key;
        this.lastSearch = now;
        const query = Array.from(this.search).every(letter => letter === key) ? key : this.search;
        const indices = this.visibleIndices();
        const start = indices.indexOf(this.active) + (query.length === 1 ? 1 : 0);
        for (let step = 0; step < indices.length; step++) {
          const index = indices[(start + step) % indices.length];
          if (this.select.options[index].label.trim().toLocaleLowerCase().startsWith(query.toLocaleLowerCase())) {
            this.activate(index);
            break;
          }
        }
      }
    }

    destroy() {
      this.close();
      const select = this.select;
      select.removeEventListener('change', this.onChange);
      select.removeEventListener('input', this.onChange);
      select.removeEventListener('focus', this.onFocus);
      select.removeEventListener('invalid', this.onInvalid);
      this.descriptors.forEach((_, property) => { delete select[property]; });
      select.classList.remove('dm-select__native');
      restoreAttribute(select, 'tabindex', this.saved.tabindex);
      restoreAttribute(select, 'aria-hidden', this.saved.ariaHidden);
      // Do not resurrect a select removed by application code.
      if (this.wrapper.contains(select)) this.wrapper.replaceWith(select);
      else this.wrapper.remove();
      controls.delete(select);
    }
  }

  function refresh(root = document) {
    if (typeof root === 'string') root = document.querySelector(root);
    if (!root) return;
    const selects = root.matches?.('select') ? [root] : Array.from(root.querySelectorAll('select'));
    for (const select of selects) {
      const control = controls.get(select);
      if (!supported(select)) { control?.destroy(); continue; }
      if (control) control.refresh();
      else controls.set(select, new SelectControl(select));
    }
    for (const [select, control] of controls) {
      if (!select.isConnected) control.destroy();
    }
  }
  const refreshAll = () => refresh(document);
  window.DMSelect = window.SelectControl = {refresh, refreshAll};

  function start() {
    refresh();
    const observer = new MutationObserver(records => {
      const roots = new Set();
      let panelChanged = false;
      for (const record of records) {
        const target = record.target.nodeType === Node.ELEMENT_NODE ? record.target : record.target.parentElement;
        const select = target?.closest('select');
        if (select) roots.add(select);
        if (record.attributeName === 'hidden' && !target?.classList.contains('dm-select') && !target?.closest('.dm-select__list')) panelChanged = true;
        if (target?.matches('fieldset') && record.attributeName === 'disabled') roots.add(target);
        if (record.attributeName === 'data-native-select') roots.add(target);
        if (record.type === 'childList') {
          for (const node of record.addedNodes) {
            if (node.nodeType === Node.ELEMENT_NODE && (node.matches('select') || node.querySelector('select'))) roots.add(node);
          }
        }
      }
      if (panelChanged) refreshAll();
      else roots.forEach(root => { if (root.isConnected) refresh(root); });
      for (const [select, control] of controls) {
        if (!select.isConnected) control.destroy();
      }
    });
    observer.observe(document.body, {
      subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ['data-native-select', 'disabled', 'multiple', 'size', 'selected', 'label', 'value', 'hidden', 'required', 'aria-label', 'aria-labelledby', 'aria-describedby', 'aria-invalid', 'aria-required'],
    });
    document.addEventListener('pointerdown', event => {
      if (current && !current.wrapper.contains(event.target) && !current.list.contains(event.target)) current.close();
    }, true);
    document.addEventListener('focusin', event => {
      if (current && !current.wrapper.contains(event.target) && !current.list.contains(event.target)) current.close();
    });
    document.addEventListener('reset', () => queueMicrotask(refreshAll), true);
    window.addEventListener('resize', () => current?.position());
    window.addEventListener('scroll', () => current?.position(), true);
    window.visualViewport?.addEventListener('resize', () => current?.position());
    window.visualViewport?.addEventListener('scroll', () => current?.position());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
