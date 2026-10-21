import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { v4 as uuidv4 } from 'uuid';
import config from '../config';
import userModel from '../models/user.model';
import { User, AuthPayload } from '../types';

const SALT_ROUNDS = 10;

export const authService = {
  async register(email: string, password: string): Promise<{ user: Omit<User, 'password'>; token: string }> {
    // Check if user exists
    if (userModel.exists(email)) {
      throw new Error('User already exists');
    }

    // Hash password
    const hashedPassword = await bcrypt.hash(password, SALT_ROUNDS);

    // Create user
    const user: User = {
      id: uuidv4(),
      email,
      password: hashedPassword,
      createdAt: new Date(),
    };

    userModel.create(user);

    // Generate token
    const token = this.generateToken({ userId: user.id, email: user.email });

    // Return user without password
    const { password: _, ...userWithoutPassword } = user;
    return { user: userWithoutPassword, token };
  },

  async login(email: string, password: string): Promise<{ user: Omit<User, 'password'>; token: string }> {
    // Find user
    const user = userModel.findByEmail(email);
    if (!user) {
      throw new Error('Invalid credentials');
    }

    // Check password
    const isValid = await bcrypt.compare(password, user.password);
    if (!isValid) {
      throw new Error('Invalid credentials');
    }

    // Generate token
    const token = this.generateToken({ userId: user.id, email: user.email });

    // Return user without password
    const { password: _, ...userWithoutPassword } = user;
    return { user: userWithoutPassword, token };
  },

  generateToken(payload: AuthPayload): string {
    return jwt.sign(payload, config.jwtSecret, {
      expiresIn: config.jwtExpiresIn as jwt.SignOptions['expiresIn'],
    });
  },

  verifyToken(token: string): AuthPayload {
    const decoded = jwt.verify(token, config.jwtSecret) as AuthPayload;
    return decoded;
  },
};

export default authService;
