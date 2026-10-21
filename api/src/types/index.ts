export interface User {
  id: string;
  email: string;
  password: string; // hashed
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

// Session parameters matching main.py arguments
export interface SessionParams {
  // Required
  goal: string;
  
  // Target (one required)
  targetUrl?: string;
  targetRequestFile?: string;
  
  // Authentication
  username?: string;
  password?: string;
  loginUrl?: string;
  usernameField?: string;      // default: "username"
  passwordField?: string;      // default: "password"
  submitField?: string;
  submitValue?: string;        // default: "Login"
  extraFormFields?: Record<string, string>;
  authTimeout?: number;        // default: 15
  insecureTls?: boolean;       // default: false
  
  // Crawling
  crawlDepth?: number;         // default: 2, range 0-3
  
  // Runtime options
  config?: string;             // default: "ai/config/models.local.yaml"
  outputDir?: string;          // default: "artifacts/phase1"
  autoApprove?: boolean;       // default: false
  printFinalState?: boolean;   // default: false
  maxTasks?: number;
  maxReplans?: number;
  maxNewTasksPerReplan?: number;
}
