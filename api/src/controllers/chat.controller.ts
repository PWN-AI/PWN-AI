import { Request, Response, NextFunction } from 'express';
import aiService from '../services/ollama.service';
import { ChatMessage } from '../types';

export const chatController = {
  /**
   * Chat completions - sends messages to the Python AI
   */
  async completions(req: Request, res: Response, next: NextFunction): Promise<void> {
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

      const response = await aiService.chat(messages as ChatMessage[], model);
      
      res.json({
        id: response.id,
        model: response.model,
        message: response.message,
        created_at: response.createdAt,
      });
    } catch (error: any) {
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
  async runSession(req: Request, res: Response, next: NextFunction): Promise<void> {
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

      const result = await aiService.runSession({
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
    } catch (error: any) {
      next(error);
    }
  },

  async listModels(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const models = await aiService.listModels();
      res.json({ models });
    } catch (error: any) {
      next(error);
    }
  },

  async health(req: Request, res: Response): Promise<void> {
    const aiAvailable = await aiService.isAvailable();
    res.json({
      status: 'ok',
      ai: aiAvailable ? 'connected' : 'disconnected',
    });
  },
};

export default chatController;
