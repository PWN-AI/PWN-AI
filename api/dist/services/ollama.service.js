"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.aiService = void 0;
const child_process_1 = require("child_process");
const path_1 = __importDefault(require("path"));
const config_1 = __importDefault(require("../config"));
const uuid_1 = require("uuid");
class AIService {
    constructor() {
        // PWN-AI project root (parent of api/)
        this.projectRoot = path_1.default.resolve(__dirname, '../../../');
    }
    /**
     * Run a pentest session using the Python AI framework
     * This calls main.py which handles all Ollama communication
     */
    async runSession(options) {
        return new Promise((resolve, reject) => {
            const args = ['main.py', '--goal', options.goal];
            if (options.targetUrl) {
                args.push('--target-url', options.targetUrl);
            }
            if (options.username) {
                args.push('--username', options.username);
            }
            if (options.password) {
                args.push('--password', options.password);
            }
            if (options.autoApprove) {
                args.push('--auto-approve');
            }
            const sessionId = (0, uuid_1.v4)();
            let output = '';
            let errorOutput = '';
            const proc = (0, child_process_1.spawn)('python', args, {
                cwd: this.projectRoot,
                env: { ...process.env },
            });
            proc.stdout?.on('data', (data) => {
                output += data.toString();
            });
            proc.stderr?.on('data', (data) => {
                errorOutput += data.toString();
            });
            proc.on('close', (code) => {
                if (code === 0) {
                    resolve({ output, sessionId });
                }
                else {
                    reject(new Error(`AI process exited with code ${code}: ${errorOutput || output}`));
                }
            });
            proc.on('error', (err) => {
                reject(new Error(`Failed to start AI process: ${err.message}`));
            });
            // Timeout after 10 minutes
            setTimeout(() => {
                proc.kill();
                reject(new Error('AI session timed out after 10 minutes'));
            }, 600000);
        });
    }
    /**
     * Simple chat using the Python AI - sends a message and gets a response
     * This spawns a Python script that uses the same LLM config as main.py
     */
    async chat(messages, model) {
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
            const proc = (0, child_process_1.spawn)('python', ['-c', pythonCode], {
                cwd: this.projectRoot,
                env: { ...process.env },
            });
            let output = '';
            let errorOutput = '';
            proc.stdout?.on('data', (data) => {
                output += data.toString();
            });
            proc.stderr?.on('data', (data) => {
                errorOutput += data.toString();
            });
            proc.on('close', (code) => {
                if (code === 0) {
                    try {
                        // Find the JSON output (last line with valid JSON)
                        const lines = output.trim().split('\n');
                        let result = null;
                        for (let i = lines.length - 1; i >= 0; i--) {
                            try {
                                result = JSON.parse(lines[i]);
                                break;
                            }
                            catch {
                                continue;
                            }
                        }
                        if (result && result.content) {
                            resolve({
                                id: (0, uuid_1.v4)(),
                                model: model || config_1.default.ollamaModel,
                                message: {
                                    role: 'assistant',
                                    content: result.content,
                                },
                                createdAt: Date.now(),
                            });
                        }
                        else {
                            reject(new Error('No valid response from AI'));
                        }
                    }
                    catch {
                        reject(new Error(`Failed to parse AI response: ${output}`));
                    }
                }
                else {
                    reject(new Error(`AI chat failed: ${errorOutput || output}`));
                }
            });
            proc.on('error', (err) => {
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
            const proc = (0, child_process_1.spawn)('python', ['-c', pythonCode], {
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
    /**
     * Check if the AI system is available
     */
    async isAvailable() {
        return new Promise((resolve) => {
            const proc = (0, child_process_1.spawn)('python', ['-c', 'import ai; print("ok")'], {
                cwd: this.projectRoot,
            });
            proc.on('close', (code) => {
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
exports.aiService = new AIService();
exports.default = exports.aiService;
//# sourceMappingURL=ollama.service.js.map