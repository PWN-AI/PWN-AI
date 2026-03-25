import { Request, Response, NextFunction } from 'express';
import authService from '../services/auth.service';

export const authController = {
  async register(req: Request, res: Response, next: NextFunction): Promise<void> {
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

      const result = await authService.register(email, password);
      res.status(201).json({
        message: 'User registered successfully',
        user: result.user,
        token: result.token,
      });
    } catch (error: any) {
      if (error.message === 'User already exists') {
        res.status(409).json({ error: error.message });
        return;
      }
      next(error);
    }
  },

  async login(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const { email, password } = req.body;

      if (!email || !password) {
        res.status(400).json({ error: 'Email and password are required' });
        return;
      }

      const result = await authService.login(email, password);
      res.json({
        message: 'Login successful',
        user: result.user,
        token: result.token,
      });
    } catch (error: any) {
      if (error.message === 'Invalid credentials') {
        res.status(401).json({ error: error.message });
        return;
      }
      next(error);
    }
  },

  async me(req: Request, res: Response): Promise<void> {
    res.json({ user: req.user });
  },
};

export default authController;
