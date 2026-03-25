import { Router } from 'express';
import authRoutes from './auth.routes';
import chatRoutes from './chat.routes';

const router = Router();

router.use('/auth', authRoutes);
router.use('/chat', chatRoutes);

// Health check endpoint (no auth required)
router.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

export default router;
