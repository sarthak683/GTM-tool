import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';
import AssignDropdown from './AssignDropdown';
const auth = vi.hoisted(() => ({ isAdmin: false, user: { id: 'me', name: 'Rep', role: 'ae' } }));
vi.mock('../lib/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../lib/api', () => ({ assignmentsApi: {} }));
vi.mock('../lib/cachedFetch', () => ({ getCachedUsers: vi.fn() }));
describe('account assignment is admin-only', () => {
  for (const role of ['ae', 'sdr', 'marketing', 'admin', 'superadmin']) {
    for (const assigned of [false, true]) {
      it(`${role}, assigned=${assigned}`, () => {
        auth.user.role = role;
        auth.isAdmin = ['admin', 'superadmin'].includes(role);
        const html = renderToStaticMarkup(<AssignDropdown entityType="company" entityId="account" currentAssignedId={assigned ? 'me' : null} currentAssignedName={assigned ? 'Rep' : null} />);
        expect(html.includes('<button')).toBe(auth.isAdmin);
        if (!auth.isAdmin) expect(html).toContain(assigned ? 'Rep' : 'Unassigned');
      });
    }
  }
});
