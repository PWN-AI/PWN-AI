"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.authService = void 0;
const bcryptjs_1 = __importDefault(require("bcryptjs"));
const jsonwebtoken_1 = __importDefault(require("jsonwebtoken"));
const uuid_1 = require("uuid");
const config_1 = __importDefault(require("../config"));
const user_model_1 = __importDefault(require("../models/user.model"));
const SALT_ROUNDS = 10;
exports.authService = {
    async register(email, password) {
        // Check if user exists
        if (user_model_1.default.exists(email)) {
            throw new Error('User already exists');
        }
        // Hash password
        const hashedPassword = await bcryptjs_1.default.hash(password, SALT_ROUNDS);
        // Create user
        const user = {
            id: (0, uuid_1.v4)(),
            email,
            password: hashedPassword,
            createdAt: new Date(),
        };
        user_model_1.default.create(user);
        // Generate token
        const token = this.generateToken({ userId: user.id, email: user.email });
        // Return user without password
        const { password: _, ...userWithoutPassword } = user;
        return { user: userWithoutPassword, token };
    },
    async login(email, password) {
        // Find user
        const user = user_model_1.default.findByEmail(email);
        if (!user) {
            throw new Error('Invalid credentials');
        }
        // Check password
        const isValid = await bcryptjs_1.default.compare(password, user.password);
        if (!isValid) {
            throw new Error('Invalid credentials');
        }
        // Generate token
        const token = this.generateToken({ userId: user.id, email: user.email });
        // Return user without password
        const { password: _, ...userWithoutPassword } = user;
        return { user: userWithoutPassword, token };
    },
    generateToken(payload) {
        return jsonwebtoken_1.default.sign(payload, config_1.default.jwtSecret, {
            expiresIn: config_1.default.jwtExpiresIn,
        });
    },
    verifyToken(token) {
        const decoded = jsonwebtoken_1.default.verify(token, config_1.default.jwtSecret);
        return decoded;
    },
};
exports.default = exports.authService;
//# sourceMappingURL=auth.service.js.map