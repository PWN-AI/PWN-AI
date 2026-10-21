const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { aiService } = require('../dist/services/ollama.service');

test('chat provider changes preserve independent Cohere embeddings', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pwnai-provider-test-'));
  const previous = {
    projectRoot: aiService.projectRoot,
    modelConfigPath: aiService.modelConfigPath,
  };
  const configPath = path.join(directory, 'ai/config/models.local.yaml');
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  fs.writeFileSync(configPath, `ollama:
  mode: "cloud"
  providers:
    local:
      base_url: "http://localhost:11434"
    cloud:
      base_url: "https://ollama.com"
models:
  head:
    provider: "cloud"
    model: "gpt-oss:120b"
  implementor:
    provider: "cloud"
    model: "gpt-oss:120b"
rag:
  embedding:
    provider: "cohere"
    model: "embed-v4.0"
`);
  aiService.projectRoot = directory;
  aiService.modelConfigPath = configPath;
  try {
    const local = await aiService.setProvider('local');
    assert.equal(local.roles.head, 'local');
    assert.equal(local.roles.implementor, 'local');
    assert.match(fs.readFileSync(configPath, 'utf8'), /provider: "cohere"/);
    const cloud = await aiService.setProvider('cloud');
    assert.equal(cloud.roles.head, 'cloud');
    assert.equal(cloud.roles.implementor, 'cloud');
    assert.match(fs.readFileSync(configPath, 'utf8'), /provider: "cohere"/);
    await assert.rejects(aiService.setProvider('invalid'), /Unsupported provider/);
  } finally {
    Object.assign(aiService, previous);
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
