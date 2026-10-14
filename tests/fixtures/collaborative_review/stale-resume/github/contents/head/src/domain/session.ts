export class SessionNotFound extends Error {
  constructor(readonly id: string) {
    super('session ' + id + ' not found');
  }
}

export interface Session {
  readonly id: string;
  readonly userId: string;
  readonly createdAt: number;
  readonly expiresAt: number;
  readonly lastSeenAt: number;
}

export interface SessionStore {
  get(id: string): Promise<Session>;
  put(session: Session): Promise<void>;
  touch(id: string, now?: number): Promise<Session>;
  delete(id: string): Promise<boolean>;
}
