import { createApp } from './app';
import config from './config';

const app = createApp();

app.listen(config.port, () => {
  console.log(`
╔═══════════════════════════════════════════════════════════╗
║                     PWN-AI API Server                     ║
╠═══════════════════════════════════════════════════════════╣
║  Status:  Running                                         ║
║  Port:    ${config.port.toString().padEnd(47)}║
║  Env:     ${config.nodeEnv.padEnd(47)}║
║  Ollama:  ${config.ollamaBaseUrl.padEnd(47)}║
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
