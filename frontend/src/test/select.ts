import { act } from 'react';

export async function openSelect(label: string) {
  const trigger = document.querySelector<HTMLButtonElement>(`button[role="combobox"][aria-label="${label}"]`);
  if (!trigger) throw new Error(`Missing select: ${label}`);
  await act(async () => { trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })) });
  return [...document.querySelectorAll<HTMLElement>('[role="option"]')];
}

export async function selectOption(label: string, text: string) {
  const options = await openSelect(label);
  const option = options.find(item => item.textContent === text);
  if (!option) throw new Error(`Missing option: ${text}`);
  await act(async () => { option.click() });
}
