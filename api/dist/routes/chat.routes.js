"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const express_1 = require("express");
const chat_controller_1 = __importDefault(require("../controllers/chat.controller"));
const auth_1 = __importDefault(require("../middleware/auth"));
const router = (0, express_1.Router)();
// All chat routes require authentication
router.use(auth_1.default);
// POST /api/chat/completions - Send messages to AI (uses Python AI)
router.post('/completions', chat_controller_1.default.completions);
// POST /api/chat/session - Run a full pentest session
router.post('/session', chat_controller_1.default.runSession);
// GET /api/chat/models - List available models from AI config
router.get('/models', chat_controller_1.default.listModels);
// GET /api/chat/provider - Read active provider config
router.get('/provider', chat_controller_1.default.getProvider);
// PUT /api/chat/provider - Update active provider config
router.put('/provider', chat_controller_1.default.updateProvider);
// GET /api/chat/health - Check AI system connection
router.get('/health', chat_controller_1.default.health);
exports.default = router;
//# sourceMappingURL=chat.routes.js.map