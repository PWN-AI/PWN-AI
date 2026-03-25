"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.chatController = void 0;
const ollama_service_1 = __importDefault(require("../services/ollama.service"));
exports.chatController = {
    /**
     * Chat completions - sends messages to the Python AI
     */
    async completions(req, res, next) {
        try {
            const { messages, model } = req.body;
            if (!messages || !Array.isArray(messages) || messages.length === 0) {
                res.status(400).json({ error: 'Messages array is required and cannot be empty' });
                return;
            }
            // Validate message format
            for (const msg of messages) {
                if (!msg.role || !msg.content) {
                    res.status(400).json({ error: 'Each message must have role and content' });
                    return;
                }
                if (!['user', 'assistant', 'system'].includes(msg.role)) {
                    res.status(400).json({ error: 'Invalid role. Must be: user, assistant, or system' });
                    return;
                }
            }
            const response = await ollama_service_1.default.chat(messages, model);
            res.json({
                id: response.id,
                model: response.model,
                message: response.message,
                created_at: response.createdAt,
            });
        }
        catch (error) {
            if (error.message.includes('Failed to start AI')) {
                res.status(503).json({ error: 'AI service unavailable. Check Python environment.' });
                return;
            }
            next(error);
        }
    },
    /**
     * Run a full pentest session
     */
    async runSession(req, res, next) {
        try {
            const { goal, targetUrl, username, password, autoApprove } = req.body;
            if (!goal) {
                res.status(400).json({ error: 'Goal is required' });
                return;
            }
            if (!targetUrl) {
                res.status(400).json({ error: 'Target URL is required' });
                return;
            }
            const result = await ollama_service_1.default.runSession({
                goal,
                targetUrl,
                username,
                password,
                autoApprove: autoApprove ?? true,
            });
            res.json({
                sessionId: result.sessionId,
                output: result.output,
                status: 'completed',
            });
        }
        catch (error) {
            next(error);
        }
    },
    async listModels(req, res, next) {
        try {
            const models = await ollama_service_1.default.listModels();
            res.json({ models });
        }
        catch (error) {
            next(error);
        }
    },
    async health(req, res) {
        const aiAvailable = await ollama_service_1.default.isAvailable();
        res.json({
            status: 'ok',
            ai: aiAvailable ? 'connected' : 'disconnected',
        });
    },
};
exports.default = exports.chatController;
//# sourceMappingURL=chat.controller.js.map