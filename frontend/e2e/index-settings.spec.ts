import { expect, test } from '@playwright/test';

test('indekslenmiş belgede bağlam ayarı uygulanır, ilerleme gösterilir ve yenilemede korunur', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  let enabled = false;
  let requested = false;
  let reads = 0;
  let job: Record<string, unknown> | null = null;
  const document = () => ({ id, logical_key: 'settings', source_name: 'test.md', lesson_title: 'Test dersi',
    content_hash: 'a'.repeat(64), version: 1, status: 'active', byte_size: 50, contextual_enrichment: enabled,
    media_type: 'text/markdown', created_at: null, replaced_at: null });
  await page.route('http://localhost:8000/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/topics') return json([]);
    if (path === '/api/documents') return json([document()]);
    if (path.endsWith('/conversion')) return json({ detail: 'Not found' }, 404);
    if (path.endsWith('/analysis')) return json({ document: document(), status: 'approved', stage: 'completed', progress: 100, topics: [], model: 'test' });
    if (path.endsWith('/index-job')) {
      if (route.request().method() === 'POST') {
        requested = route.request().postDataJSON().contextual_enrichment;
        reads = 0;
        job = { document_id: id, status: 'queued', stage: 'queued', progress: 0, message: 'Sıraya alındı', contextual_enrichment: requested };
      } else if (job && ++reads > 1) {
        enabled = requested;
        job = { ...job, status: 'completed', stage: 'completed', progress: 100, message: 'İndeks yenilendi' };
      } else if (job) {
        job = { ...job, status: 'running', stage: 'contextualizing', progress: 35, message: 'Arama bağlamı hazırlanıyor' };
      }
      return json(job);
    }
    if (path === '/api/profiles/bootstrap') return json({ id: '33333333-3333-4333-8333-333333333333', client_id: 'settings-e2e', display_name: 'Test', progress: {} });
    if (path === '/api/feedback/state') return json({ keys: [] });
    return json({});
  });
  await page.goto('/kaynaklar');
  await page.locator('button.document-toggle').click();
  const checkbox = page.getByRole('checkbox', { name: /Bu belge için arama bağlamını zenginleştir/ });
  await expect(checkbox).not.toBeChecked();
  await checkbox.check();
  await page.getByRole('button', { name: 'Ayarı uygula ve yeniden indeksle' }).click();
  await expect(page.getByRole('progressbar', { name: 'İndeks yenileme ilerlemesi' })).toBeVisible();
  await expect(checkbox).toBeDisabled();
  await expect(page.getByText('Arama bağlamı: Açık', { exact: true })).toBeVisible({ timeout: 10_000 });
  await page.reload();
  await page.locator('button.document-toggle').click();
  await expect(checkbox).toBeChecked();
  await checkbox.uncheck();
  await page.getByRole('button', { name: 'Ayarı uygula ve yeniden indeksle' }).click();
  await expect(page.getByText('Arama bağlamı: Kapalı', { exact: true })).toBeVisible({ timeout: 10_000 });
});
