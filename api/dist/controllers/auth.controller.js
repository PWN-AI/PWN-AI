"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.authController = void 0;
const auth_service_1 = __importDefault(require("../services/auth.service"));
exports.authController = {
    async register(req, res, next) {
        try {
            const { email, password } = req.body;
            if (!email || !password) {
                res.status(400).json({ error: 'Email and password are required' });
                return;
            }
            if (typeof email !== 'string' || !email.includes('@')) {
                res.status(400).json({ error: 'Invalid email format' });
                return;
            }
            if (typeof password !== 'string' || password.length < 6) {
                res.status(400).json({ error: 'Password must be at least 6 characters' });
                return;
            }
            const result = await auth_service_1.default.register(email, password);
            res.status(201).json({
                message: 'User registered successfully',
                user: result.user,
                token: result.token,
            });
        }
        catch (error) {
            if (error.message === 'User already exists') {
                res.status(409).json({ error: error.message });
                return;
            }
            next(error);
        }
    },
    async login(req, res, next) {
        try {
            const { email, password } = req.body;
            if (!email || !password) {
                res.status(400).json({ error: 'Email and password are required' });
                return;
            }
            const result = await auth_service_1.default.login(email, password);
            res.json({
                message: 'Login successful',
                user: result.user,
                token: result.token,
            });
        }
        catch (error) {
            if (error.message === 'Invalid credentials') {
                res.status(401).json({ error: error.message });
                return;
            }
            next(error);
        }
    },
    async me(req, res) {
        res.json({ user: req.user });
    },
};
exports.default = exports.authController;
//# sourceMappingURL=auth.controller.js.map