"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const express_1 = require("express");
const auth_controller_1 = __importDefault(require("../controllers/auth.controller"));
const auth_1 = __importDefault(require("../middleware/auth"));
const router = (0, express_1.Router)();
// Public routes
router.post('/register', auth_controller_1.default.register);
router.post('/login', auth_controller_1.default.login);
// Protected routes
router.get('/me', auth_1.default, auth_controller_1.default.me);
exports.default = router;
//# sourceMappingURL=auth.routes.js.map