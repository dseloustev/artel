export interface Session {
  readonly id: string;
  readonly userId: string;
  readonly createdAt: number;
  readonly expiresAt: number;
}

export interface SessionStore {
  get(id: string): Promise<Session | null>;
  put(session: Session): Promise<void>;
  delete(id: string): Promise<boolean>;
}
