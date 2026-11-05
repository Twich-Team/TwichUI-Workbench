import type { components } from './api.generated';
export type Task = components['schemas']['Task'];
export type Receipt = components['schemas']['Receipt'];
export type Decision = components['schemas']['Decision'];
type Session = components['schemas']['Session'];

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const demo = sessionStorage.getItem('workbench-demo');
  if (demo && /^\/api\/(work|backlog|records|history|activity)([/?]|$)/.test(path) && !path.includes('source=pilot') && !path.includes('/records/pilot/')) path += `${path.includes('?') ? '&' : '?'}demo=${encodeURIComponent(demo)}`;
  let response: Response;
  try { response = await fetch(path, { ...init, credentials: 'same-origin', signal: AbortSignal.timeout(8000) }); }
  catch { throw new ApiError(0, 'Connection interrupted. Your decision may have been saved. Retry to recover the same receipt.'); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof data.detail === 'string' ? data.detail : 'The request could not be completed. Try again.');
  }
  return response.json();
}
export const taskKey = (id: string) => ['task', id];
export const loadTask = (id: string) => request<Task>(`/api/tasks/${encodeURIComponent(id)}`);
export async function saveDecision(taskId: string, decision: Decision) {
  const session = await request<Session>('/api/session');
  return request<Receipt>(`/api/tasks/${encodeURIComponent(taskId)}/decisions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
    body: JSON.stringify(decision),
  });
}
export function readDraft(taskId: string): Decision | null {
  try {
    const raw = localStorage.getItem(`review:${taskId}`);
    if (!raw) return null;
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== 'object') return null;
    const draft = data as Decision;
    return typeof draft.command_id === 'string' && typeof draft.version === 'string' && draft.choices && typeof draft.notes === 'string' ? draft : null;
  } catch { return null; }
}

export async function mutate<T>(path: string, body: unknown, method = 'POST'): Promise<T> {
  const session = await request<Session>('/api/session');
  return request<T>(path, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token }, body: JSON.stringify(body) });
}
