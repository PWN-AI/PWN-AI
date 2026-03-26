import { Request, Response, NextFunction } from 'express';
export declare const chatController: {
    /**
     * Chat completions - sends messages to the Python AI
     */
    completions(req: Request, res: Response, next: NextFunction): Promise<void>;
    /**
     * Run a full pentest session with all parameters
     */
    runSession(req: Request, res: Response, next: NextFunction): Promise<void>;
    listModels(req: Request, res: Response, next: NextFunction): Promise<void>;
    health(req: Request, res: Response): Promise<void>;
};
export default chatController;
//# sourceMappingURL=chat.controller.d.ts.map