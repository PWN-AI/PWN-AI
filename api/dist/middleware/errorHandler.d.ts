import { Request, Response, NextFunction } from 'express';
export interface AppError extends Error {
    statusCode?: number;
}
export declare const errorHandler: (err: AppError, req: Request, res: Response, next: NextFunction) => void;
export default errorHandler;
//# sourceMappingURL=errorHandler.d.ts.map