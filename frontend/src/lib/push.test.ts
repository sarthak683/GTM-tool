import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { enablePush, getSubscriptionState } from './push';
import { pushApi } from './api';
vi.mock('./api', () => ({ pushApi: { getVapidPublicKey: vi.fn(), listSubscriptions: vi.fn(), subscribe: vi.fn(), unsubscribe: vi.fn() } }));
const endpoint = 'https://push.example.invalid/device';
let getSubscription: ReturnType<typeof vi.fn>;
let requestPermission: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.resetAllMocks();
  getSubscription = vi.fn().mockResolvedValue({ endpoint });
  requestPermission = vi.fn().mockResolvedValue('granted');
  vi.stubGlobal('PushManager', function () {});
  vi.stubGlobal('Notification', { permission: 'granted', requestPermission });
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { ready: Promise.resolve({ pushManager: { getSubscription } }) } });
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Android Chrome' });
  vi.mocked(pushApi.getVapidPublicKey).mockResolvedValue({ configured: true, publicKey: 'AQID' });
});
afterEach(() => { vi.unstubAllGlobals(); });
describe('push pairing status', () => {
  it('does not report enabled when the server lost the subscription', async () => {
    vi.mocked(pushApi.listSubscriptions).mockResolvedValue([]);
    const state = await getSubscriptionState();
    expect(state.subscribed).toBe(false);
    expect(state.reason).toContain("isn't paired");
  });
  it('does not report another CRM account subscription as enabled', async () => {
    vi.mocked(pushApi.listSubscriptions).mockResolvedValue([{ id: 'other', endpoint: 'https://push.example.invalid/other', user_agent: null, label: null }]);
    expect((await getSubscriptionState()).subscribed).toBe(false);
  });
  it('reports enabled when browser and CRM account agree', async () => {
    vi.mocked(pushApi.listSubscriptions).mockResolvedValue([{ id: 'device', endpoint, user_agent: null, label: null }]);
    expect((await getSubscriptionState()).subscribed).toBe(true);
  });
  it('shows installation instructions for iPhone Safari', async () => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'iPhone Safari' });
    const state = await getSubscriptionState();
    expect(state.subscribed).toBe(false);
    expect(state.reason).toContain('Add to Home Screen');
  });
  it('asks permission before waiting for the service worker', async () => {
    vi.stubGlobal('Notification', { permission: 'default', requestPermission });
    let readyRead = false;
    Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: {
      get ready() { readyRead = true; return Promise.resolve({ pushManager: { getSubscription } }); },
    } });
    getSubscription.mockResolvedValue({ endpoint, toJSON: () => ({ endpoint, keys: { p256dh: 'test', auth: 'test' } }) });
    requestPermission.mockImplementation(() => { expect(readyRead).toBe(false); return Promise.resolve('granted'); });
    expect((await enablePush()).ok).toBe(true);
    expect(pushApi.subscribe).toHaveBeenCalledOnce();
  });
});
