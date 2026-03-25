import { User, AuthPayload } from '../types';
export declare const authService: {
    register(email: string, password: string): Promise<{
        user: Omit<User, "password">;
        token: string;
    }>;
    login(email: string, password: string): Promise<{
        user: Omit<User, "password">;
        token: string;
    }>;
    generateToken(payload: AuthPayload): string;
    verifyToken(token: string): AuthPayload;
};
export default authService;
//# sourceMappingURL=auth.service.d.ts.map