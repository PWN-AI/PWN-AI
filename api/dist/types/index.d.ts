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
    size: number;
    digest: string;
    modified_at: string;
}
export interface AuthPayload {
    userId: string;
    email: string;
}
export interface JwtPayload extends AuthPayload {
    iat: number;
    exp: number;
}
//# sourceMappingURL=index.d.ts.map