export interface User {
    id: string;
    email: string;
    password: string;
    createdAt: Date;
}
export interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
}
export interface ChatRequest {
    messages: ChatMessage[];
    model?: string;
    stream?: boolean;
}
export interface ChatResponse {
    id: string;
    model: string;
    message: ChatMessage;
    createdAt: number;
}
export interface OllamaModel {
    name: string;
    role: string;
    provider: string;
}
export interface ProviderConfig {
    activeProvider: string;
    fallbackProvider: string;
    availableProviders: string[];
    roles: Record<string, string>;
}
export interface AuthPayload {
    userId: string;
    email: string;
}
export interface JwtPayload extends AuthPayload {
    iat: number;
    exp: number;
}
export interface SessionParams {
    goal: string;
    targetUrl?: string;
    targetRequestFile?: string;
    username?: string;
    password?: string;
    loginUrl?: string;
    usernameField?: string;
    passwordField?: string;
    submitField?: string;
    submitValue?: string;
    extraFormFields?: Record<string, string>;
    authTimeout?: number;
    insecureTls?: boolean;
    crawlDepth?: number;
    config?: string;
    outputDir?: string;
    autoApprove?: boolean;
    printFinalState?: boolean;
    maxTasks?: number;
    maxReplans?: number;
    maxActionsPerObjective?: number;
    maxNewTasksPerReplan?: number;
}
//# sourceMappingURL=index.d.ts.map