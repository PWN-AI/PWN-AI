import { User } from '../types';
export declare const userModel: {
    findById(id: string): User | undefined;
    findByEmail(email: string): User | undefined;
    create(user: User): User;
    exists(email: string): boolean;
    getAll(): User[];
    clear(): void;
};
export default userModel;
//# sourceMappingURL=user.model.d.ts.map