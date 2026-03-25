"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.createApp = createApp;
const express_1 = __importDefault(require("express"));
const cors_1 = __importDefault(require("cors"));
const helmet_1 = __importDefault(require("helmet"));
const routes_1 = __importDefault(require("./routes"));
const errorHandler_1 = __importDefault(require("./middleware/errorHandler"));
function createApp() {
    const app = (0, express_1.default)();
    // Security middleware
    app.use((0, helmet_1.default)());
    // CORS configuration
    app.use((0, cors_1.default)({
        origin: process.env.CORS_ORIGIN || '*',
        methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
        allowedHeaders: ['Content-Type', 'Authorization'],
    }));
    // Body parsing
    app.use(express_1.default.json({ limit: '10mb' }));
    app.use(express_1.default.urlencoded({ extended: true }));
    // API routes
    app.use('/api', routes_1.default);
    // Root endpoint
    app.get('/', (req, res) => {
        res.json({
            name: 'PWN-AI API',
            version: '1.0.0',
            description: 'Express.js backend for PWN-AI pentesting framework',
            endpoints: {
                health: 'GET /api/health',
                auth: {
                    register: 'POST /api/auth/register',
                    login: 'POST /api/auth/login',
                    me: 'GET /api/auth/me (requires auth)',
                },
                chat: {
                    completions: 'POST /api/chat/completions (requires auth)',
                    models: 'GET /api/chat/models (requires auth)',
                    health: 'GET /api/chat/health (requires auth)',
                },
            },
        });
    });
    // 404 handler
    app.use((req, res) => {
        res.status(404).json({ error: 'Not found' });
    });
    // Error handler
    app.use(errorHandler_1.default);
    return app;
}
exports.default = createApp;
//# sourceMappingURL=app.js.map