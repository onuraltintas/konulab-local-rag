import { expect, test } from '@playwright/test';

test('ayarlar sayfası sağlayıcıyı doğrulayıp kalıcı model seçimi uygular', async ({ page }) => {
  let savedProvider = 'ollama';
  const settings = () => ({
    text_provider: savedProvider,
    ollama_base_url: 'http://127.0.0.1:11434',
    ollama_model: 'qwen3.8:27b',
    vllm_base_url: 'http://127.0.0.1:8001',
    vllm_model: 'qwen3.8:27b',
    vision_provider: 'ollama',
    vision_model: 'qwen3.8:27b',
    context_length: 32768,
    embedding_model: 'ytu-ce-cosmos/turkish-e5-large',
    embedding_device: 'cuda',
    providers: [
      { provider: 'ollama', base_url: 'http://127.0.0.1:11434', reachable: true, error: null, models: [{ id: 'qwen3.8:27b', label: 'qwen3.8:27b', size_bytes: 1, parameter_size: '27B', quantization: 'Q4' }] },
      { provider: 'vllm', base_url: 'http://127.0.0.1:8001', reachable: true, error: null, models: [{ id: 'qwen3.8:27b', label: 'qwen3.8:27b', size_bytes: null, parameter_size: null, quantization: null }] },
    ],
  });

  await page.route('http://localhost:8000/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/settings/models' && request.method() === 'GET') return json(settings());
    if (path === '/api/settings/models/test') return json({ ok: true, message: 'Bağlantı ve seçili metin/görsel model listeleri doğrulandı.', text_model: 'qwen3.8:27b' });
    if (path === '/api/settings/models' && request.method() === 'PUT') {
      savedProvider = JSON.parse(request.postData() ?? '{}').text_provider;
      return json(settings());
    }
    if (path === '/api/profiles/bootstrap') return json({ id: 'profile', client_id: 'settings-e2e', display_name: 'E2E öğrenci', created_at: null, updated_at: null, progress: {} });
    if (path === '/api/topics' || path === '/api/documents') return json([]);
    return json({});
  });

  await page.goto('/ayarlar');
  await expect(page.getByRole('heading', { name: 'Yerel modelleri güvenle yönet.' })).toBeVisible();
  await page.locator('select').first().selectOption('vllm');
  await page.getByRole('button', { name: 'Bağlantıyı ve modeli dene' }).click();
  await expect(page.getByText('Bağlantı ve seçili metin/görsel model listeleri doğrulandı.')).toBeVisible();
  await page.getByRole('button', { name: 'Doğrula ve uygula' }).click();
  await expect(page.getByText('Ayarlar uygulandı. Etkin metin modeli: qwen3.8:27b.')).toBeVisible();
  expect(savedProvider).toBe('vllm');
});
