import { describe, expect, it, vi } from 'vitest';
import source from '../../public/sw.js?raw';
function loadWorker(client?: { url: string; navigate: ReturnType<typeof vi.fn>; focus: ReturnType<typeof vi.fn> }) {
  const handlers: Record<string, (event: unknown) => void> = {};
  const worker = {
    addEventListener: (type: string, handler: (event: unknown) => void) => { handlers[type] = handler; },
    location: { origin: 'https://crm.example.invalid' },
    registration: { showNotification: vi.fn().mockResolvedValue(undefined) },
    clients: { matchAll: vi.fn().mockResolvedValue(client ? [client] : []), openWindow: vi.fn().mockResolvedValue(undefined) },
  };
  new Function('self', source)(worker);
  return { handlers, worker };
}
describe('notification dialing', () => {
  it.each(['null', 'reject', 'focus-reject'])('opens dial bridge when client navigation fails: %s', async (failure) => {
    const client = { url: 'https://crm.example.invalid/contacts', navigate: vi.fn(), focus: vi.fn() };
    client.focus.mockRejectedValue(new Error('focus failed'));
    if (failure === 'null') client.navigate.mockResolvedValue(null);
    else if (failure === 'reject') client.navigate.mockRejectedValue(new Error('navigate failed'));
    else client.navigate.mockResolvedValue(client);
    const { handlers, worker } = loadWorker(client);
    let pending!: Promise<unknown>;
    handlers.notificationclick({ notification: { close: vi.fn(), data: { tel: '+12025550101', contact_name: 'Test' } }, waitUntil: (promise: Promise<unknown>) => { pending = promise; } });
    await pending;
    expect(worker.clients.openWindow).toHaveBeenCalledWith('/dial?tel=%2B12025550101&name=Test');
  });
  it.each([null, [], 'text'])('shows a notification for non-object payload: %j', async (payload) => {
    const { handlers, worker } = loadWorker();
    let pending!: Promise<unknown>;
    handlers.push({ data: { json: () => payload }, waitUntil: (promise: Promise<unknown>) => { pending = promise; } });
    await pending;
    expect(worker.registration.showNotification).toHaveBeenCalledWith('Beacon CRM', expect.any(Object));
  });
});
