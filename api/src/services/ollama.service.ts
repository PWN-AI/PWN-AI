import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import config from '../config';
import { ChatMessage, ChatResponse, OllamaModel, SessionParams } from '../types';
import { v4 as uuidv4 } from 'uuid';

class AIService {
  private projectRoot: string;

  constructor() {
    // PWN-AI project root (parent of api/)
    this.projectRoot = path.resolve(__dirname, '../../../');
  }

  /**
   * Run a pentest session using the Python AI framework
   * This calls main.py which handles all Ollama communication
   */
  async runSession(params: SessionParams): Promise<{ output: string; sessionId: string }> {
    return new Promise((resolve, reject) => {
      const args = ['main.py'];

      // Required: goal
      args.push('--goal', params.goal);

      // Target (one of these required)
      if (params.targetUrl) {
        args.push('--target-url', params.targetUrl);
      }
      if (params.targetRequestFile) {
        args.push('--target-request-file', params.targetRequestFile);
      }

      // Authentication
      if (params.username) {
        args.push('--username', params.username);
      }
      if (params.password) {
        args.push('--password', params.password);
      }
      if (params.loginUrl) {
        args.push('--login-url', params.loginUrl);
      }
      if (params.usernameField) {
        args.push('--username-field', params.usernameField);
      }
      if (params.passwordField) {
        args.push('--password-field', params.passwordField);
      }
      if (params.submitField) {
        args.push('--submit-field', params.submitField);
      }
      if (params.submitValue) {
        args.push('--submit-value', params.submitValue);
      }
      if (params.extraFormFields) {
        args.push('--extra-form-fields', JSON.stringify(params.extraFormFields));
      }
      if (params.authTimeout !== undefined) {
        args.push('--auth-timeout', params.authTimeout.toString());
      }
      if (params.insecureTls) {
        args.push('--insecure-tls');
      }

      // Crawling
      if (params.crawlDepth !== undefined) {
        args.push('--crawl-depth', params.crawlDepth.toString());
      }

      // Runtime options
      if (params.config) {
        args.push('--config', params.config);
      }
      if (params.outputDir) {
        args.push('--output-dir', params.outputDir);
      }
      if (params.autoApprove) {
        args.push('--auto-approve');
      }
      if (params.printFinalState) {
        args.push('--print-final-state');
      }
      if (params.maxTasks !== undefined) {
        args.push('--max-tasks', params.maxTasks.toString());
      }
      if (params.maxReplans !== undefined) {
        args.push('--max-replans', params.maxReplans.toString());
      }
      if (params.maxNewTasksPerReplan !== undefined) {
        args.push('--max-new-tasks-per-replan', params.maxNewTasksPerReplan.toString());
      }

      const sessionId = uuidv4();
      let output = '';
      let errorOutput = '';

      const proc: ChildProcess = spawn('python', args, {
        cwd: this.projectRoot,
        env: { ...process.env },
      });

      proc.stdout?.on('data', (data: Buffer) => {
        output += data.toString();
      });

      proc.stderr?.on('data', (data: Buffer) => {
        errorOutput += data.toString();
      });

      proc.on('close', (code: number | null) => {
        if (code === 0) {
          resolve({ output, sessionId });
        } else {
          reject(new Error(`AI process exited with code ${code}: ${errorOutput || output}`));
        }
      });

      proc.on('error', (err: Error) => {
        reject(new Error(`Failed to start AI process: ${err.message}`));
      });

      // Timeout after 30 minutes for full sessions
      setTimeout(() => {
        proc.kill();
        reject(new Error('AI session timed out after 30 minutes'));
      }, 1800000);
    });
  }

  /**
   * Simple chat using the Python AI - sends a message and gets a response
   * This spawns a Python script that uses the same LLM config as main.py
   */
  async chat(messages: ChatMessage[], model?: string): Promise<ChatResponse> {
    return new Promise((resolve, reject) => {
      // Format messages for the Python script
      const messagesJson = JSON.stringify(messages);
      
      // Use a simple Python script to chat via the AI's Ollama client
      const pythonCode = `
import sys
import json
sys.path.insert(0, '.')
from ai.llm.ollama_client import build_ollama_clients
from pathlib import Path

messages_raw = '''${messagesJson.replace(/'/g, "\\'")}'''
messages = json.loads(messages_raw)

# Build LLM client using existing config
config_path = Path("ai/config/models.local.yaml")
head_llm, impl_llm, cfg = build_ollama_clients(config_path)

# Convert to LangChain message format
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

lc_messages = []
for msg in messages:
    if msg["role"] == "system":
        lc_messages.append(SystemMessage(content=msg["content"]))
    elif msg["role"] == "user":
        lc_messages.append(HumanMessage(content=msg["content"]))
    elif msg["role"] == "assistant":
        lc_messages.append(AIMessage(content=msg["content"]))

# Use head LLM for chat
response = head_llm.invoke(lc_messages)
content = getattr(response, "content", str(response))

# Output as JSON
print(json.dumps({"content": content}))
`;

      const proc: ChildProcess = spawn('python', ['-c', pythonCode], {
        cwd: this.projectRoot,
        env: { ...process.env },
      });

      let output = '';
      let errorOutput = '';

      proc.stdout?.on('data', (data: Buffer) => {
        output += data.toString();
      });

      proc.stderr?.on('data', (data: Buffer) => {
        errorOutput += data.toString();
      });

      proc.on('close', (code: number | null) => {
        if (code === 0) {
          try {
            // Find the JSON output (last line with valid JSON)
            const lines = output.trim().split('\n');
            let result = null;
            for (let i = lines.length - 1; i >= 0; i--) {
              try {
                result = JSON.parse(lines[i]);
                break;
              } catch {
                continue;
              }
            }
            
            if (result && result.content) {
              resolve({
                id: uuidv4(),
                model: model || config.ollamaModel,
                message: {
                  role: 'assistant',
                  content: result.content,
                },
                createdAt: Date.now(),
              });
            } else {
              reject(new Error('No valid response from AI'));
            }
          } catch {
            reject(new Error(`Failed to parse AI response: ${output}`));
          }
        } else {
          reject(new Error(`AI chat failed: ${errorOutput || output}`));
        }
      });

      proc.on('error', (err: Error) => {
        reject(new Error(`Failed to start AI: ${err.message}`));
      });

      // Timeout after 5 minutes for chat
      setTimeout(() => {
        proc.kill();
        reject(new Error('AI chat timed out'));
      }, 300000);
    });
  }

  /**
   * List available models from the AI config
   */
  async listModels(): Promise<OllamaModel[]> {
    return new Promise((resolve) => {
      const pythonCode = `
import sys
import json
import yaml
from pathlib import Path

config_path = Path("ai/config/models.local.yaml")
with open(config_path, "r") as f:
    cfg = yaml.safe_load(f)

models = cfg.get("models", {})

result = []
for role, model_cfg in models.items():
    if isinstance(model_cfg, dict) and "model" in model_cfg:
        result.append({
            "name": model_cfg["model"],
            "role": role,
            "provider": model_cfg.get("provider", "local")
        })

print(json.dumps(result))
`;

      const proc: ChildProcess = spawn('python', ['-c', pythonCode], {
        cwd: this.projectRoot,
      });

      let output = '';

      proc.stdout?.on('data', (data: Buffer) => {
        output += data.toString();
      });

      proc.on('close', (code: number | null) => {
        if (code === 0) {
          try {
            const models = JSON.parse(output.trim());
            resolve(models);
          } catch {
            resolve([]);
          }
        } else {
          resolve([]);
        }
      });

      proc.on('error', () => {
        resolve([]);
      });
    });
  }

  /**
   * Check if the AI system is available
   */
  async isAvailable(): Promise<boolean> {
    return new Promise((resolve) => {
      const proc: ChildProcess = spawn('python', ['-c', 'import ai; print("ok")'], {
        cwd: this.projectRoot,
      });

      proc.on('close', (code: number | null) => {
        resolve(code === 0);
      });

      proc.on('error', () => {
        resolve(false);
      });

      setTimeout(() => {
        proc.kill();
        resolve(false);
      }, 5000);
    });
  }
}

export const aiService = new AIService();
export default aiService;
