import { describe, expect, test } from 'vitest';
import { MemorySessionStore } from '../src/domain/memory_session_store';
import { SessionNotFound } from '../src/domain/session';

describe('MemorySessionStore', () => {
  test('put then get returns the session', async () => {
    const store = new MemorySessionStore();
    const session = { id: 's1', userId: 'u1', createdAt: 0, expiresAt: 60, lastSeenAt: 0 };
    await store.put(session);
    expect(await store.get('s1')).toEqual(session);
  });

  test('missing session rejects with SessionNotFound', async () => {
    const store = new MemorySessionStore();
    await expect(store.get('nope')).rejects.toBeInstanceOf(SessionNotFound);
  });

  test('touch updates lastSeenAt', async () => {
    const store = new MemorySessionStore();
    await store.put({ id: 's2', userId: 'u1', createdAt: 0, expiresAt: 60, lastSeenAt: 0 });
    expect((await store.touch('s2', 120)).lastSeenAt).toBe(120);
  });
});
