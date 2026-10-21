import { Router } from 'express';
import chatController from '../controllers/chat.controller';
import authMiddleware from '../middleware/auth';

const router = Router();

// All chat routes require authentication
router.use(authMiddleware);

// POST /api/chat/completions - Send messages to AI (uses Python AI)
router.post('/completions', chatController.completions);

// POST /api/chat/session - Run a full pentest session
router.post('/session', chatController.runSession);

// GET /api/chat/models - List available models from AI config
router.get('/models', chatController.listModels);

// GET /api/chat/provider - Read active provider config
router.get('/provider', chatController.getProvider);

// PUT /api/chat/provider - Update active provider config
router.put('/provider', chatController.updateProvider);

// GET /api/chat/health - Check AI system connection
router.get('/health', chatController.health);

export default router;
