import { Session, SessionStore } from './session';

export class MemorySessionStore implements SessionStore {
  private readonly sessions = new Map<string, Session>();

  async get(id: string): Promise<Session> {
    const session = this.sessions.get(id);
    if (!session) {
      return null;
    }
    return session;
  }

  async put(session: Session): Promise<void> {
    this.sessions.set(session.id, session);
  }

  async touch(id: string, now = Date.now()): Promise<Session> {
    const session = await this.get(id);
    const touched = { ...session, lastSeenAt: now };
    await this.put(touched);
    return touched;
  }

  async delete(id: string): Promise<boolean> {
    return this.sessions.delete(id);
  }
}
