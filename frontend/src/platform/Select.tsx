import { useCallback, useId, useLayoutEffect, useRef, useState } from 'react';
import type { AriaAttributes } from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import { Icon } from './ui';
import './select.css';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectProps extends AriaAttributes {
  id?: string;
  value: string;
  options: readonly SelectOption[];
  onValueChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

// Radix reserves the empty string for its placeholder; an empty option is a
// legitimate value for defaults such as “使用服务器默认模型”.
const encode = (value: string) => `value:${value}`;

export function Select({ id, value, options, onValueChange, placeholder = '请选择', disabled, ...aria }: SelectProps) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const triggerRef = useCallback((node: HTMLButtonElement | null) => {
    // Native dialogs live in the top layer. Keep their menus in that layer too.
    setContainer(node?.closest('dialog') ?? null);
  }, []);
  const selected = options.some(option => option.value === value);
  return <SelectPrimitive.Root value={selected ? encode(value) : ''} onValueChange={next => onValueChange(next.slice(6))} disabled={disabled || !options.length}>
    <SelectPrimitive.Trigger ref={triggerRef} id={id} className="app-select-trigger" {...aria}>
      <SelectPrimitive.Value placeholder={placeholder} />
      <SelectPrimitive.Icon className="app-select-chevron"><Icon name="chevron" size={16} /></SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
    <SelectPrimitive.Portal container={container}>
      <SelectPrimitive.Content className="app-select-content" position="popper" sideOffset={6} collisionPadding={12}>
        <SelectPrimitive.ScrollUpButton className="app-select-scroll app-select-scroll-up"><Icon name="chevron" size={14} /></SelectPrimitive.ScrollUpButton>
        <SelectPrimitive.Viewport className="app-select-viewport">
          {options.map(option => <SelectPrimitive.Item className="app-select-option" key={option.value} value={encode(option.value)} disabled={option.disabled}>
            <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
            <SelectPrimitive.ItemIndicator className="app-select-indicator"><Icon name="check" size={16} /></SelectPrimitive.ItemIndicator>
          </SelectPrimitive.Item>)}
        </SelectPrimitive.Viewport>
        <SelectPrimitive.ScrollDownButton className="app-select-scroll"><Icon name="chevron" size={14} /></SelectPrimitive.ScrollDownButton>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  </SelectPrimitive.Root>;
}

/** Editable suggestions share the same menu style while accepting custom IDs. */
export function Combobox({ id, value, displayValue, options, onValueChange, placeholder, disabled, ...aria }: SelectProps & { displayValue?: string }) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const listId = `${inputId}-options`;
  const input = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState({ side: 'bottom', maxHeight: 320 });
  const [menu, setMenu] = useState({ open: false, query: '', active: -1 });
  const filtered = options.filter(option => !option.disabled && `${option.label} ${option.value}`.toLocaleLowerCase().includes(menu.query.toLocaleLowerCase()));
  const open = menu.open && !disabled && filtered.length > 0;
  const active = open && menu.active >= 0 && menu.active < filtered.length ? menu.active : -1;
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      if (!input.current || !menuRef.current) return;
      const rect = input.current.getBoundingClientRect();
      const below = window.innerHeight - rect.bottom - 12;
      const above = rect.top - 12;
      const side = below < Math.min(320, menuRef.current.scrollHeight) && above > below ? 'top' : 'bottom';
      const maxHeight = Math.max(0, Math.min(320, side === 'top' ? above : below));
      setPlacement(current => current.side === side && current.maxHeight === maxHeight ? current : { side, maxHeight });
    };
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => { window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true) };
  }, [open, filtered.length]);
  const choose = (next: string) => {
    onValueChange(next);
    input.current?.focus();
    setMenu({ open: false, query: '', active: -1 });
  };
  const move = (next: number) => {
    setMenu(current => ({ ...current, open: true, active: next }));
    document.getElementById(`${listId}-${next}`)?.scrollIntoView({ block: 'nearest' });
  };
  return <div className={`app-combobox${options.length ? ' has-options' : ''}`} data-state={open ? 'open' : 'closed'}>
    <div className="app-combobox-control">
      <input {...aria} ref={input} id={inputId} role={options.length ? 'combobox' : undefined} autoComplete="off" aria-autocomplete={options.length ? 'list' : undefined} aria-expanded={options.length ? open : undefined} aria-controls={open ? listId : undefined} aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined} value={displayValue ?? value} placeholder={placeholder} disabled={disabled}
        onFocus={() => setMenu({ open: true, query: '', active: -1 })}
        onBlur={() => setMenu(current => ({ ...current, open: false }))}
        onChange={event => { onValueChange(event.target.value); setMenu({ open: true, query: event.target.value, active: -1 }) }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (filtered.length) move(event.key === 'ArrowDown' ? Math.min(active + 1, filtered.length - 1) : active < 0 ? filtered.length - 1 : Math.max(active - 1, 0));
          } else if (open && (event.key === 'Home' || event.key === 'End')) {
            event.preventDefault(); move(event.key === 'Home' ? 0 : filtered.length - 1);
          } else if (open && event.key === 'Enter' && active >= 0) {
            event.preventDefault(); choose(filtered[active].value);
          } else if (open && event.key === 'Escape') {
            event.preventDefault(); event.stopPropagation(); setMenu(current => ({ ...current, open: false }));
          } else if (event.key === 'Tab') setMenu(current => ({ ...current, open: false }));
        }} />
      {options.length > 0 && <button type="button" className="app-combobox-toggle" aria-label="显示模型建议" tabIndex={-1} disabled={disabled} onMouseDown={event => event.preventDefault()} onClick={() => { input.current?.focus(); setMenu({ open: !open, query: '', active: -1 }) }}><Icon name="chevron" size={16} /></button>}
    </div>
    {open && <div ref={menuRef} className="app-combobox-menu app-select-content" role="listbox" id={listId} aria-label={aria['aria-label']} data-placement={placement.side} style={{ maxHeight: placement.maxHeight }}>
      {filtered.map((option, index) => <div key={option.value} id={`${listId}-${index}`} role="option" aria-selected={value === option.value} className="app-select-option app-combobox-option" data-highlighted={active === index ? '' : undefined} data-state={value === option.value ? 'checked' : 'unchecked'} onPointerDown={event => event.preventDefault()} onPointerMove={() => setMenu(current => ({ ...current, active: index }))} onClick={() => choose(option.value)}>
        <span>{option.label}</span>
        {value === option.value && <span className="app-select-indicator"><Icon name="check" size={16} /></span>}
      </div>)}
    </div>}
  </div>;
}
