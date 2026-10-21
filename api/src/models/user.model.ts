import { User } from '../types';

// In-memory user storage (can be replaced with database later)
const users: Map<string, User> = new Map();

export const userModel = {
  findById(id: string): User | undefined {
    return users.get(id);
  },

  findByEmail(email: string): User | undefined {
    for (const user of users.values()) {
      if (user.email === email) {
        return user;
      }
    }
    return undefined;
  },

  create(user: User): User {
    users.set(user.id, user);
    return user;
  },

  exists(email: string): boolean {
    return this.findByEmail(email) !== undefined;
  },

  // For debugging/testing
  getAll(): User[] {
    return Array.from(users.values());
  },

  clear(): void {
    users.clear();
  },
};

export default userModel;
