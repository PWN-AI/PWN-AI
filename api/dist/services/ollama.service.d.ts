import { ChatMessage, ChatResponse, OllamaModel, SessionParams } from '../types';
declare class AIService {
    private projectRoot;
    constructor();
    /**
     * Run a pentest session using the Python AI framework
     * This calls main.py which handles all Ollama communication
     */
    runSession(params: SessionParams): Promise<{
        output: string;
        sessionId: string;
    }>;
    /**
     * Simple chat using the Python AI - sends a message and gets a response
     * This spawns a Python script that uses the same LLM config as main.py
     */
    chat(messages: ChatMessage[], model?: string): Promise<ChatResponse>;
    /**
     * List available models from the AI config
     */
    listModels(): Promise<OllamaModel[]>;
    /**
     * Check if the AI system is available
     */
    isAvailable(): Promise<boolean>;
}
export declare const aiService: AIService;
export default aiService;
//# sourceMappingURL=ollama.service.d.ts.map