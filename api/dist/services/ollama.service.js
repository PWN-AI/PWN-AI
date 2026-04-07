"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.aiService = void 0;
const child_process_1 = require("child_process");
const path_1 = __importDefault(require("path"));
const fs_1 = __importDefault(require("fs"));
const config_1 = __importDefault(require("../config"));
const uuid_1 = require("uuid");
class AIService {
    constructor() {
        // PWN-AI project root (parent of api/)
        this.projectRoot = path_1.default.resolve(__dirname, '../../../');
        this.modelConfigPath = path_1.default.join(this.projectRoot, 'ai/config/models.local.yaml');
        // Resolve Python path - try .venv first, then configured path, then fallback to 'python'
        this.pythonPath = this.resolvePythonPath();
        console.log(`[AI Service] Using Python: ${this.pythonPath}`);
    }
    resolvePythonPath() {
        // Check configured path (could be absolute or relative)
        const configuredPath = config_1.default.pythonPath;
        // If it's an absolute path, use it directly
        if (path_1.default.isAbsolute(configuredPath)) {
            if (fs_1.default.existsSync(configuredPath)) {
                return configuredPath;
            }
        }
        // Try relative to project root
        const venvPath = path_1.default.join(this.projectRoot, configuredPath);
        if (fs_1.default.existsSync(venvPath)) {
            return venvPath;
        }
        // Try common venv locations
        const commonPaths = [
            path_1.default.join(this.projectRoot, '.venv/bin/python'),
            path_1.default.join(this.projectRoot, 'venv/bin/python'),
            path_1.default.join(this.projectRoot, '.venv/Scripts/python.exe'), // Windows
            path_1.default.join(this.projectRoot, 'venv/Scripts/python.exe'), // Windows
        ];
        for (const p of commonPaths) {
            if (fs_1.default.existsSync(p)) {
                return p;
            }
        }
        // Fallback to system python
        return 'python';
    }
    createStreamCancelledError(message = 'Stream cancelled by client') {
        const error = new Error(message);
        error.code = 'STREAM_CANCELLED';
        return error;
    }
    readModelConfigFile() {
        return fs_1.default.readFileSync(this.modelConfigPath, 'utf8');
    }
    extractFallbackProvider(configContent) {
        const match = configContent.match(/^(\s*mode:\s*["']?)([^"'\r\n]+)(["']?.*)$/m);
        return match?.[2]?.trim() || 'local';
    }
    extractAvailableProviders(configContent) {
        const providersSectionMatch = configContent.match(/^\s{2}providers:\s*\r?\n([\s\S]*?)(?=^\S|\r?\nmodels:)/m);
        if (!providersSectionMatch) {
            return [];
        }
        return Array.from(providersSectionMatch[1].matchAll(/^\s{4}([a-zA-Z0-9_-]+):\s*$/gm)).map((match) => match[1]);
    }
    buildSessionArgs(params) {
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
    buildChatPythonScript() {
        return `
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

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
    createOperation(options) {
        const proc = (0, child_process_1.spawn)(this.pythonPath, options.args, {
            cwd: this.projectRoot,
            env: { ...process.env },
        });
        let stdout = '';
        let stderr = '';
        let settled = false;
        let timeout;
        let resolvePromise;
        let rejectPromise;
        const cleanup = () => {
            if (timeout) {
                clearTimeout(timeout);
                timeout = undefined;
            }
        };
        const safeResolve = (value) => {
            if (settled) {
                return;
            }
            settled = true;
            cleanup();
            resolvePromise(value);
        };
        const safeReject = (error) => {
            if (settled) {
                return;
            }
            settled = true;
            cleanup();
            rejectPromise(error);
        };
        const promise = new Promise((resolve, reject) => {
            resolvePromise = resolve;
            rejectPromise = reject;
            proc.stdout?.on('data', (data) => {
                const chunk = data.toString();
                stdout += chunk;
                options.callbacks?.onStdout?.(chunk);
            });
            proc.stderr?.on('data', (data) => {
                const chunk = data.toString();
                stderr += chunk;
                options.callbacks?.onStderr?.(chunk);
            });
            proc.on('close', (code) => {
                if (settled) {
                    return;
                }
                if (code !== 0) {
                    safeReject(new Error(`${options.exitErrorLabel}: ${stderr || stdout}`));
                    return;
                }
                try {
                    safeResolve(options.resolveResult(stdout, stderr));
                }
                catch (error) {
                    safeReject(error);
                }
            });
            proc.on('error', (err) => {
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
    async runSession(params) {
        return this.streamSession(params).promise;
    }
    streamSession(params, callbacks) {
        const sessionId = (0, uuid_1.v4)();
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
    async chat(messages, model) {
        return this.streamChat(messages, model).promise;
    }
    streamChat(messages, model, callbacks) {
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
                    id: (0, uuid_1.v4)(),
                    model: model || config_1.default.ollamaModel,
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
    async listModels() {
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
            const proc = (0, child_process_1.spawn)(this.pythonPath, ['-c', pythonCode], {
                cwd: this.projectRoot,
            });
            let output = '';
            proc.stdout?.on('data', (data) => {
                output += data.toString();
            });
            proc.on('close', (code) => {
                if (code === 0) {
                    try {
                        const models = JSON.parse(output.trim());
                        resolve(models);
                    }
                    catch {
                        resolve([]);
                    }
                }
                else {
                    resolve([]);
                }
            });
            proc.on('error', () => {
                resolve([]);
            });
        });
    }
    async getProviderConfig() {
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
    async setProvider(provider) {
        const currentConfig = await this.getProviderConfig();
        if (!currentConfig.availableProviders.includes(provider)) {
            throw new Error(`Unsupported provider "${provider}"`);
        }
        let nextContent = this.readModelConfigFile();
        nextContent = nextContent.replace(/^(\s*mode:\s*["']?)([^"'\r\n]+)(["']?.*)$/m, `$1${provider}$3`);
        nextContent = nextContent.replace(/^(\s+provider:\s*["']?)([^"'\r\n]+)(["']?.*)$/gm, `$1${provider}$3`);
        fs_1.default.writeFileSync(this.modelConfigPath, nextContent, 'utf8');
        return this.getProviderConfig();
    }
    /**
     * Check if the AI system is available
     */
    async isAvailable() {
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
            const proc = (0, child_process_1.spawn)(this.pythonPath, ['-c', pythonCode], {
                cwd: this.projectRoot,
                env: { ...process.env },
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
            proc.on('close', (code) => {
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
exports.aiService = new AIService();
exports.default = exports.aiService;
//# sourceMappingURL=ollama.service.js.map