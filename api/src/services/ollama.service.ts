import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import config from '../config';
import { ChatMessage, ChatResponse, OllamaModel, ProviderConfig, SessionParams } from '../types';
import { v4 as uuidv4 } from 'uuid';

interface StreamCallbacks {
  onStdout?: (chunk: string) => void;
  onStderr?: (chunk: string) => void;
}

interface StreamOperation<T> {
  promise: Promise<T>;
  cancel: () => void;
}

class AIService {
  private projectRoot: string;
  private pythonPath: string;
  private modelConfigPath: string;

  constructor() {
    // PWN-AI project root (parent of api/)
    this.projectRoot = path.resolve(__dirname, '../../../');
    this.modelConfigPath = path.join(this.projectRoot, 'ai/config/models.local.yaml');

    // Resolve Python path - try .venv first, then configured path, then fallback to 'python'
    this.pythonPath = this.resolvePythonPath();
    console.log(`[AI Service] Using Python: ${this.pythonPath}`);
  }

  private resolvePythonPath(): string {
    // Check configured path (could be absolute or relative)
    const configuredPath = config.pythonPath;

    // If it's an absolute path, use it directly
    if (path.isAbsolute(configuredPath)) {
      if (fs.existsSync(configuredPath)) {
        return configuredPath;
      }
    }

    // Try relative to project root
    const venvPath = path.join(this.projectRoot, configuredPath);
    if (fs.existsSync(venvPath)) {
      return venvPath;
    }

    // Try common venv locations
    const commonPaths = [
      path.join(this.projectRoot, '.venv/bin/python'),
      path.join(this.projectRoot, 'venv/bin/python'),
      path.join(this.projectRoot, '.venv/Scripts/python.exe'), // Windows
      path.join(this.projectRoot, 'venv/Scripts/python.exe'),  // Windows
    ];

    for (const p of commonPaths) {
      if (fs.existsSync(p)) {
        return p;
      }
    }

    // Fallback to system python
    return 'python';
  }

  private createStreamCancelledError(message = 'Stream cancelled by client'): Error & { code: string } {
    const error = new Error(message) as Error & { code: string };
    error.code = 'STREAM_CANCELLED';
    return error;
  }

  private readModelConfigFile(): string {
    return fs.readFileSync(this.modelConfigPath, 'utf8');
  }

  private extractFallbackProvider(configContent: string): string {
    const match = configContent.match(/^(\s*mode:\s*["']?)([^"'\r\n]+)(["']?.*)$/m);
    return match?.[2]?.trim() || 'local';
  }

  private extractAvailableProviders(configContent: string): string[] {
    const providersSectionMatch = configContent.match(/^\s{2}providers:\s*\r?\n([\s\S]*?)(?=^\S|\r?\nmodels:)/m);
    if (!providersSectionMatch) {
      return [];
    }

    return Array.from(providersSectionMatch[1].matchAll(/^\s{4}([a-zA-Z0-9_-]+):\s*$/gm)).map((match) => match[1]);
  }

  private buildSessionArgs(params: SessionParams): string[] {
    const args = ['-u', 'main.py'];

    args.push('--goal', params.goal);

    if (params.targetUrl) {
      args.push('--target-url', params.targetUrl);
    }
    if (params.targetRequestFile) {
      args.push('--target-request-file', params.targetRequestFile);
    }

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

    if (params.crawlDepth !== undefined) {
      args.push('--crawl-depth', params.crawlDepth.toString());
    }

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

    return args;
  }

  private buildChatPythonScript(): string {
    return `
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ai.llm.ollama_client import build_ollama_clients
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

messages = json.loads(sys.stdin.read() or "[]")
config_path = Path("ai/config/models.local.yaml")
head_llm, _impl_llm, _cfg = build_ollama_clients(config_path)

lc_messages = []
for msg in messages:
    role = msg.get("role")
    content = msg.get("content", "")
    if role == "system":
        lc_messages.append(SystemMessage(content=content))
    elif role == "assistant":
        lc_messages.append(AIMessage(content=content))
    else:
        lc_messages.append(HumanMessage(content=content))

for chunk in head_llm.stream(lc_messages):
    token = ""
    if hasattr(chunk, "content"):
        token = chunk.content or ""
    elif isinstance(chunk, str):
        token = chunk
    if token:
        sys.stdout.write(token)
        sys.stdout.flush()
`;
  }

  private createOperation<T>(options: {
    args: string[];
    stdinInput?: string;
    timeoutMs: number;
    callbacks?: StreamCallbacks;
    startErrorMessage: string;
    timeoutMessage: string;
    exitErrorLabel: string;
    resolveResult: (stdout: string, stderr: string) => T;
  }): StreamOperation<T> {
    const proc: ChildProcess = spawn(this.pythonPath, options.args, {
      cwd: this.projectRoot,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });

    let stdout = '';
    let stderr = '';
    let settled = false;
    let timeout: NodeJS.Timeout | undefined;
    let resolvePromise!: (value: T) => void;
    let rejectPromise!: (reason?: unknown) => void;

    const cleanup = () => {
      if (timeout) {
        clearTimeout(timeout);
        timeout = undefined;
      }
    };

    const safeResolve = (value: T) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolvePromise(value);
    };

    const safeReject = (error: unknown) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      rejectPromise(error);
    };

    const promise = new Promise<T>((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;

      proc.stdout?.on('data', (data: Buffer) => {
        const chunk = data.toString();
        stdout += chunk;
        options.callbacks?.onStdout?.(chunk);
      });

      proc.stderr?.on('data', (data: Buffer) => {
        const chunk = data.toString();
        stderr += chunk;
        options.callbacks?.onStderr?.(chunk);
      });

      proc.on('close', (code: number | null) => {
        if (settled) {
          return;
        }

        if (code !== 0) {
          safeReject(new Error(`${options.exitErrorLabel}: ${stderr || stdout}`));
          return;
        }

        try {
          safeResolve(options.resolveResult(stdout, stderr));
        } catch (error) {
          safeReject(error);
        }
      });

      proc.on('error', (err: Error) => {
        safeReject(new Error(`${options.startErrorMessage}: ${err.message}`));
      });

      if (options.stdinInput !== undefined) {
        proc.stdin?.write(options.stdinInput);
        proc.stdin?.end();
      }

      timeout = setTimeout(() => {
        proc.kill();
        safeReject(new Error(options.timeoutMessage));
      }, options.timeoutMs);
    });

    return {
      promise,
      cancel: () => {
        if (settled) {
          return;
        }
        proc.kill();
        safeReject(this.createStreamCancelledError());
      },
    };
  }

  /**
   * Run a pentest session using the Python AI framework
   * This calls main.py which handles all Ollama communication
   */
  async runSession(params: SessionParams): Promise<{ output: string; sessionId: string }> {
    return this.streamSession(params).promise;
  }

  streamSession(
    params: SessionParams,
    callbacks?: StreamCallbacks
  ): StreamOperation<{ output: string; sessionId: string }> {
    const sessionId = uuidv4();

    return this.createOperation({
      args: this.buildSessionArgs(params),
      callbacks,
      timeoutMs: 1800000,
      startErrorMessage: 'Failed to start AI process',
      timeoutMessage: 'AI session timed out after 30 minutes',
      exitErrorLabel: 'AI process failed',
      resolveResult: (stdout, stderr) => ({
        output: stdout || stderr,
        sessionId,
      }),
    });
  }

  /**
   * Simple chat using the Python AI - sends a message and gets a response
   * This spawns a Python script that uses the same LLM config as main.py
   */
  async chat(messages: ChatMessage[], model?: string): Promise<ChatResponse> {
    return this.streamChat(messages, model).promise;
  }

  streamChat(messages: ChatMessage[], model?: string, callbacks?: StreamCallbacks): StreamOperation<ChatResponse> {
    return this.createOperation({
      args: ['-u', '-c', this.buildChatPythonScript()],
      stdinInput: JSON.stringify(messages),
      callbacks,
      timeoutMs: 300000,
      startErrorMessage: 'Failed to start AI',
      timeoutMessage: 'AI chat timed out',
      exitErrorLabel: 'AI chat failed',
      resolveResult: (stdout) => {
        if (!stdout.trim()) {
          throw new Error('No valid response from AI');
        }

        return {
          id: uuidv4(),
          model: model || config.ollamaModel,
          message: {
            role: 'assistant',
            content: stdout,
          },
          createdAt: Date.now(),
        };
      },
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

      const proc: ChildProcess = spawn(this.pythonPath, ['-c', pythonCode], {
        cwd: this.projectRoot,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
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

  async getProviderConfig(): Promise<ProviderConfig> {
    const configContent = this.readModelConfigFile();
    const models = await this.listModels();
    const roles = Object.fromEntries(models.map((model) => [model.role, model.provider]));
    const providers = Array.from(new Set(models.map((model) => model.provider)));

    return {
      activeProvider: providers.length === 1 ? providers[0] : 'mixed',
      fallbackProvider: this.extractFallbackProvider(configContent),
      availableProviders: this.extractAvailableProviders(configContent),
      roles,
    };
  }

  async setProvider(provider: string): Promise<ProviderConfig> {
    const currentConfig = await this.getProviderConfig();
    if (!currentConfig.availableProviders.includes(provider)) {
      throw new Error(`Unsupported provider "${provider}"`);
    }

    let nextContent = this.readModelConfigFile();

    nextContent = nextContent.replace(
      /^(\s*mode:\s*["']?)([^"'\r\n]+)(["']?.*)$/m,
      `$1${provider}$3`
    );

    nextContent = nextContent.replace(
      /^(\s+provider:\s*["']?)([^"'\r\n]+)(["']?.*)$/gm,
      `$1${provider}$3`
    );

    fs.writeFileSync(this.modelConfigPath, nextContent, 'utf8');

    return this.getProviderConfig();
  }

  /**
   * Check if the AI system is available
   */
  async isAvailable(): Promise<boolean> {
    return new Promise((resolve) => {
      const pythonCode = `
import sys
from pathlib import Path

sys.path.insert(0, ".")

import requests
from ai.llm.ollama_client import load_model_config, resolve_model_connection

cfg = load_model_config(Path("ai/config/models.local.yaml"))
conn = resolve_model_connection(cfg, "head")
base_url = str(conn.get("base_url", "")).rstrip("/")
if not base_url:
    raise RuntimeError("Missing Ollama base URL")

response = requests.get(f"{base_url}/api/version", headers=conn.get("headers", {}), timeout=15)
response.raise_for_status()
print("ok")
`;

      const proc: ChildProcess = spawn(this.pythonPath, ['-c', pythonCode], {
        cwd: this.projectRoot,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      });

      let settled = false;
      const timeout = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        proc.kill();
        resolve(false);
      }, 20000);

      proc.on('close', (code: number | null) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timeout);
        resolve(code === 0);
      });

      proc.on('error', () => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timeout);
        resolve(false);
      });
    });
  }
}

export const aiService = new AIService();
export default aiService;
