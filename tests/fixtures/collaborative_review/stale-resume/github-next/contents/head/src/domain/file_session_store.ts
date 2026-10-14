import { Session, SessionNotFound, SessionStore } from './session';

export class FileSessionStore implements SessionStore {
  constructor(private readonly directory: string) {}

  async get(id: string): Promise<Session> {
    throw new SessionNotFound(id);
  }

  async put(session: Session): Promise<void> {
    throw new Error('file store is not implemented yet: ' + session.id);
  }

  async touch(id: string, now = Date.now()): Promise<Session> {
    throw new Error('file store is not implemented yet: ' + id + ' at ' + now);
  }

  async delete(id: string): Promise<boolean> {
    return false;
  }
}
