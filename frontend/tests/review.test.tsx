import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { Outcome, ReviewForm, taskDisplayState } from '../src/App';
import { Dialog, DialogContent } from '../src/components/ui/dialog';
import type { Task, Receipt } from '../src/api';
import fixture from '../../fixtures/tasks.json';

const task = { ...fixture[0], version: 'a'.repeat(64), review_available: true, latest_command: null } as Task;
const receipt = { id: 'saved-id', task_id: task.id, version: task.version, status: 'pending',
  payload: { command_id: 'saved-id', version: task.version, choices: { follow: 'confirm' }, notes: '' },
  reviewed_evidence: task.evidence, reviewed_revision: 1, message: 'Decision saved. Waiting for the worker.', created_at: '2026-01-01T00:00:00+00:00', finished_at: null } as Receipt;
function form(value = task) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Dialog open><DialogContent><ReviewForm task={value} /></DialogContent></Dialog></QueryClientProvider>);
}
afterEach(() => vi.unstubAllGlobals());

describe('review semantics', () => {
  it('requires a direct choice for every behavior and selection does not submit', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    form();
    expect(screen.getByRole('button', { name: 'Save review decisions' })).toBeDisabled();
    for (const radio of screen.getAllByLabelText('Confirm this behavior')) await userEvent.click(radio);
    expect(screen.getByRole('button', { name: 'Save review decisions' })).toBeEnabled();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('explains that requesting changes retains the hold', async () => {
    form();
    await userEvent.click(screen.getAllByLabelText('Needs changes')[0]);
    expect(screen.getByText(/Requesting changes keeps the candidate held/)).toBeVisible();
  });
  it('preserves choices but refuses to bind them to refreshed evidence', async () => {
    const view = form();
    await userEvent.click(screen.getAllByLabelText('Confirm this behavior')[0]);
    view.rerender(<QueryClientProvider client={new QueryClient()}><Dialog open><DialogContent><ReviewForm task={{ ...task, version: 'b'.repeat(64) }} /></DialogContent></Dialog></QueryClientProvider>);
    expect(screen.getByText('The evidence has changed.')).toBeVisible();
    expect(screen.getAllByLabelText('Confirm this behavior')[0]).toBeChecked();
    expect(screen.queryByRole('button', { name: 'Save review decisions' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Open refreshed review' }));
    expect(screen.getAllByLabelText('Confirm this behavior')[0]).not.toBeChecked();
  });
  it('retains the same durable command ID after a connection failure', async () => {
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/session') return { ok: true, json: async () => ({ csrf_token: 'token' }) };
      requests.push(String(init?.body)); throw new Error('connection lost');
    }));
    form();
    for (const radio of screen.getAllByLabelText('Confirm this behavior')) await userEvent.click(radio);
    await userEvent.click(screen.getByRole('button', { name: 'Save review decisions' }));
    await screen.findByRole('alert');
    await userEvent.click(screen.getByRole('button', { name: 'Retry same decision' }));
    await screen.findByRole('alert');
    expect(requests).toHaveLength(2);
    expect(requests[0]).toEqual(requests[1]);
    expect(JSON.parse(localStorage.getItem(`review:${task.id}`)!)).toMatchObject({ version: task.version });
  });
  it('recovers an acknowledged receipt after reload and freezes the saved choices', () => {
    localStorage.setItem(`review:${task.id}`, JSON.stringify(receipt.payload));
    form({ ...task, review_available: false, latest_command: receipt });
    expect(screen.getByText('Saved · Pending application')).toBeVisible();
    expect(screen.getAllByLabelText('Confirm this behavior')[0]).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save review decisions' })).not.toBeInTheDocument();
  });
  it('shows the saved choices in a different browser without local storage', () => {
    form({ ...task, review_available: false, latest_command: receipt });
    expect(screen.getAllByLabelText('Confirm this behavior')[0]).toBeChecked();
    expect(screen.getByText('Saved · Pending application')).toBeVisible();
  });
  it.each([
    ['pending', 'Saved · Pending application'], ['applied', 'Applied'],
    ['stale', 'Needs another look'], ['failed', 'Could not apply'],
  ] as const)('labels the %s outcome explicitly', (status, label) => {
    render(<Outcome receipt={{ ...receipt, status }} />);
    expect(screen.getByText(label)).toBeVisible();
  });
});

// A pending command no longer asks the person to decide again.
it('shows pending instead of a second actionable review', () => {
  expect(taskDisplayState({ ...task, review_available: false, latest_command: receipt }))
    .toBe('Decision saved · Waiting to apply');
});
