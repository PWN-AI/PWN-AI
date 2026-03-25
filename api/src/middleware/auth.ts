import { Request, Response, NextFunction } from 'express';
import authService from '../services/auth.service';
import { AuthPayload } from '../types';

// Extend Express Request type
declare global {
  namespace Express {
    interface Request {
      user?: AuthPayload;
    }
  }
}

export const authMiddleware = (req: Request, res: Response, next: NextFunction): void => {
  try {
    const authHeader = req.headers.authorization;

    if (!authHeader) {
      res.status(401).json({ error: 'No authorization header provided' });
      return;
    }

    const parts = authHeader.split(' ');
    if (parts.length !== 2 || parts[0] !== 'Bearer') {
      res.status(401).json({ error: 'Invalid authorization format. Use: Bearer <token>' });
      return;
    }

    const token = parts[1];
    const decoded = authService.verifyToken(token);
    
    req.user = decoded;
    next();
  } catch (error: any) {
    if (error.name === 'TokenExpiredError') {
      res.status(401).json({ error: 'Token has expired' });
      return;
    }
    if (error.name === 'JsonWebTokenError') {
      res.status(401).json({ error: 'Invalid token' });
      return;
    }
    res.status(401).json({ error: 'Authentication failed' });
  }
};

export default authMiddleware;
