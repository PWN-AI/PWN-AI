import express, { Application } from 'express';
import cors from 'cors';
import helmet from 'helmet';
import fs from 'fs';
import path from 'path';
import routes from './routes';
import errorHandler from './middleware/errorHandler';

export function createApp(): Application {
  const app = express();
  const frontendDist = path.resolve(__dirname, '../../web/dist');
  const frontendIndex = path.join(frontendDist, 'index.html');
  const hasFrontendBuild = fs.existsSync(frontendIndex);
  const frontendPages: Record<string, string> = {
    '/': frontendIndex,
    '/auth': path.join(frontendDist, 'auth', 'index.html'),
    '/chat': path.join(frontendDist, 'chat', 'index.html'),
    '/session': path.join(frontendDist, 'session', 'index.html'),
  };

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

  if (hasFrontendBuild) {
    app.use(express.static(frontendDist));

    Object.entries(frontendPages).forEach(([route, filePath]) => {
      if (!fs.existsSync(filePath)) {
        return;
      }

      const routesToServe = route === '/' ? ['/'] : [route, `${route}/`];
      app.get(routesToServe, (req, res) => {
        res.sendFile(filePath);
      });
    });
  } else {
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
  }

  // 404 handler
  app.use((req, res) => {
    res.status(404).json({ error: 'Not found' });
  });

  // Error handler
  app.use(errorHandler);

  return app;
}

export default createApp;
