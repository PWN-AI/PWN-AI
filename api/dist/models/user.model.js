"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.userModel = void 0;
// In-memory user storage (can be replaced with database later)
const users = new Map();
exports.userModel = {
    findById(id) {
        return users.get(id);
    },
    findByEmail(email) {
        for (const user of users.values()) {
            if (user.email === email) {
                return user;
            }
        }
        return undefined;
    },
    create(user) {
        users.set(user.id, user);
        return user;
    },
    exists(email) {
        return this.findByEmail(email) !== undefined;
    },
    // For debugging/testing
    getAll() {
        return Array.from(users.values());
    },
    clear() {
        users.clear();
    },
};
exports.default = exports.userModel;
//# sourceMappingURL=user.model.js.map