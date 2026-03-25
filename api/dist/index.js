"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const app_1 = require("./app");
const config_1 = __importDefault(require("./config"));
const app = (0, app_1.createApp)();
app.listen(config_1.default.port, () => {
    console.log(`
╔═══════════════════════════════════════════════════════════╗
║                     PWN-AI API Server                     ║
╠═══════════════════════════════════════════════════════════╣
║  Status:  Running                                         ║
║  Port:    ${config_1.default.port.toString().padEnd(47)}║
║  Env:     ${config_1.default.nodeEnv.padEnd(47)}║
║  Ollama:  ${config_1.default.ollamaBaseUrl.padEnd(47)}║
╠═══════════════════════════════════════════════════════════╣
║  Endpoints:                                               ║
║    GET  /              - API info                         ║
║    GET  /api/health    - Health check                     ║
║    POST /api/auth/register - Register user                ║
║    POST /api/auth/login    - Login                        ║
║    POST /api/chat/completions - Chat with AI (auth)       ║
║    GET  /api/chat/models - List models (auth)             ║
╚═══════════════════════════════════════════════════════════╝
  `);
});
//# sourceMappingURL=index.js.map