import { Request, Response, NextFunction } from 'express';
import { AuthPayload } from '../types';
declare global {
    namespace Express {
        interface Request {
            user?: AuthPayload;
        }
    }
}
export declare const authMiddleware: (req: Request, res: Response, next: NextFunction) => void;
export default authMiddleware;
//# sourceMappingURL=auth.d.ts.map