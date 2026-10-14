import { SessionStore } from '../domain/session';

export async function loadForReview(store: SessionStore, id: string) {
  const session = await store.get(id);
  if (!session) {
    return null;
  }
  return session;
}
