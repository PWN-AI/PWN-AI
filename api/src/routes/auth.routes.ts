import { Router } from 'express';
import authController from '../controllers/auth.controller';
import authMiddleware from '../middleware/auth';

const router = Router();

// Public routes
router.post('/register', authController.register);
router.post('/login', authController.login);

// Protected routes
router.get('/me', authMiddleware, authController.me);

export default router;
