import { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Combobox, Select } from './Select';
import { openSelect, selectOption } from '../test/select';

let container: HTMLDivElement;
let root: Root;
const options = [
  { value: '', label: '默认模型' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'disabled', label: '暂不可用', disabled: true },
  { value: 'openai', label: 'OpenAI' },
];

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() });

it('selects domain values and can return to an empty default option', async () => {
  const changed = vi.fn();
  function Field() {
    const [value, setValue] = useState('');
    return <Select aria-label="模型" value={value} options={options} onValueChange={next => { setValue(next); changed(next) }} />;
  }
  await act(async () => root.render(<Field />));
  await selectOption('模型', 'DeepSeek');
  expect(changed).toHaveBeenLastCalledWith('deepseek');
  expect(container.querySelector('[role=combobox]')?.textContent).toBe('DeepSeek');
  await selectOption('模型', '默认模型');
  expect(changed).toHaveBeenLastCalledWith('');
  expect(container.querySelector('[role=combobox]')?.textContent).toBe('默认模型');
});

it('supports keyboard navigation, skips disabled options and cancels without changing the value', async () => {
  const changed = vi.fn();
  await act(async () => root.render(<Select aria-label="模型" value="deepseek" options={options} onValueChange={changed} />));
  await openSelect('模型');
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) });
  expect(document.activeElement?.textContent).toBe('DeepSeek');
  await act(async () => {
    document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  expect(document.activeElement?.textContent).toBe('OpenAI');
  await act(async () => { document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) });
  expect(changed).not.toHaveBeenCalled();
  expect(document.querySelector('[role=listbox]')).toBeNull();
});

it('renders the menu inside a native dialog and disables empty or unavailable controls', async () => {
  const changed = vi.fn();
  await act(async () => root.render(<dialog open><Select aria-label="模型" value="" options={options} onValueChange={changed} /></dialog>));
  await openSelect('模型');
  expect(container.querySelector('dialog [role=listbox]')).not.toBeNull();
  await act(async () => root.render(<Select aria-label="模型" value="" options={[]} onValueChange={changed} placeholder="暂无模型" />));
  expect(container.querySelector<HTMLButtonElement>('[role=combobox]')?.disabled).toBe(true);
  expect(container.textContent).toContain('暂无模型');
  await act(async () => root.render(<Select aria-label="模型" value="" options={options} onValueChange={changed} disabled />));
  expect(container.querySelector<HTMLButtonElement>('[role=combobox]')?.disabled).toBe(true);
});

it('filters editable suggestions, selects with Enter, and accepts custom model IDs', async () => {
  const models = [{ value: 'deepseek-flash', label: 'DeepSeek Flash' }, { value: 'deepseek-pro', label: 'DeepSeek Pro' }];
  function Field() {
    const [value, setValue] = useState('');
    return <Combobox aria-label="模型 ID" value={value} options={models} onValueChange={setValue} />;
  }
  await act(async () => root.render(<Field />));
  const input = container.querySelector('input')!;
  const type = async (value: string) => act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await type('pro');
  expect(container.querySelectorAll('[role=option]')).toHaveLength(1);
  await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })) });
  await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })) });
  expect(input.value).toBe('deepseek-pro');
  expect(input.getAttribute('aria-expanded')).toBe('false');
  await type('my-private-model');
  expect(input.value).toBe('my-private-model');
  expect(container.querySelector('[role=listbox]')).toBeNull();
});
