"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.authMiddleware = void 0;
const auth_service_1 = __importDefault(require("../services/auth.service"));
const authMiddleware = (req, res, next) => {
    try {
        const authHeader = req.headers.authorization;
        if (!authHeader) {
            res.status(401).json({ error: 'No authorization header provided' });
            return;
        }
        const parts = authHeader.split(' ');
        if (parts.length !== 2 || parts[0] !== 'Bearer') {
            res.status(401).json({ error: 'Invalid authorization format. Use: Bearer <token>' });
            return;
        }
        const token = parts[1];
        const decoded = auth_service_1.default.verifyToken(token);
        req.user = decoded;
        next();
    }
    catch (error) {
        if (error.name === 'TokenExpiredError') {
            res.status(401).json({ error: 'Token has expired' });
            return;
        }
        if (error.name === 'JsonWebTokenError') {
            res.status(401).json({ error: 'Invalid token' });
            return;
        }
        res.status(401).json({ error: 'Authentication failed' });
    }
};
exports.authMiddleware = authMiddleware;
exports.default = exports.authMiddleware;
//# sourceMappingURL=auth.js.map