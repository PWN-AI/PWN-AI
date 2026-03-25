import express, { Application } from 'express';
import cors from 'cors';
import helmet from 'helmet';
import routes from './routes';
import errorHandler from './middleware/errorHandler';

export function createApp(): Application {
  const app = express();

  // Security middleware
  app.use(helmet());
  
  // CORS configuration
  app.use(cors({
    origin: process.env.CORS_ORIGIN || '*',
    methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization'],
  }));

  // Body parsing
  app.use(express.json({ limit: '10mb' }));
  app.use(express.urlencoded({ extended: true }));

  // API routes
  app.use('/api', routes);

  // Root endpoint
  app.get('/', (req, res) => {
    res.json({
      name: 'PWN-AI API',
      version: '1.0.0',
      description: 'Express.js backend for PWN-AI pentesting framework',
      endpoints: {
        health: 'GET /api/health',
        auth: {
          register: 'POST /api/auth/register',
          login: 'POST /api/auth/login',
          me: 'GET /api/auth/me (requires auth)',
        },
        chat: {
          completions: 'POST /api/chat/completions (requires auth)',
          models: 'GET /api/chat/models (requires auth)',
          health: 'GET /api/chat/health (requires auth)',
        },
      },
    });
  });

  // 404 handler
  app.use((req, res) => {
    res.status(404).json({ error: 'Not found' });
  });

  // Error handler
  app.use(errorHandler);

  return app;
}

export default createApp;
