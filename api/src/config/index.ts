import dotenv from 'dotenv';
import path from 'path';
import fs from 'fs';

const rootEnvPath = path.resolve(__dirname, '../../../.env');
const apiEnvPath = path.resolve(__dirname, '../../.env');
const envPath = fs.existsSync(rootEnvPath) ? rootEnvPath : apiEnvPath;

dotenv.config({ path: envPath });

export const config = {
  // Server
  port: parseInt(process.env.PORT || '3000', 10),
  nodeEnv: process.env.NODE_ENV || 'development',

  // JWT
  jwtSecret: process.env.JWT_SECRET || 'default-secret-change-in-production',
  jwtExpiresIn: process.env.JWT_EXPIRES_IN || '24h',

  // Ollama (kept for reference)
  ollamaBaseUrl: process.env.OLLAMA_BASE_URL || 'http://localhost:11434',
  ollamaModel: process.env.OLLAMA_MODEL || 'qwen2.5-coder:7b',

  // Python - use .venv if available
  pythonPath: process.env.PYTHON_PATH || '.venv/bin/python',
};

export default config;
