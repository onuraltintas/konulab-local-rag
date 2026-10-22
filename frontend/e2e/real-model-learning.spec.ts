import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

// Opt-in: dedicated API/database/Qdrant collection and an external PDF are required.
// The route only proxies transport to that API; no responses/models are mocked.
test('gerçek PDF → görsel model → onay → Qdrant → öğrenme → bağlam aç/kapat', async ({ page, request }, testInfo) => {
  test.skip(!process.env['KONULAB_REAL_E2E'], 'Gerçek modellerle ayrı doğrulama ortamı gerektirir.');
  test.setTimeout(600_000);
  const api = process.env['KONULAB_REAL_API'] ?? 'http://127.0.0.1:8002';
  const pdfPath = process.env['KONULAB_REAL_PDF'];
  if (!pdfPath) throw new Error('KONULAB_REAL_PDF belirtilmeli.');
  await page.route('http://localhost:8000/api/**', async route => {
    const original = new URL(route.request().url());
    await route.continue({ url: `${api}${original.pathname}${original.search}` });
  });
  const fetchJson = async (path: string) => {
    const response = await request.get(`${api}${path}`);
    expect(response.ok()).toBe(true);
    return response.json();
  };
  expect(new URL(api).port, 'Üretim API portu gerçek test için kullanılmamalı.').not.toBe('8000');
  expect(await fetchJson('/api/documents'), 'Boş ve ayrı test ortamı gereklidir.').toEqual([]);
  let createdId: string | null = null;
  try {
  await page.goto('/kaynaklar');
  await page.locator('#source-file').setInputFiles(pdfPath);
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/documents/convert') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Kuyruğu başlat' }).click();
  const uploadResponse = await uploaded;
  expect(uploadResponse.ok(), await uploadResponse.text()).toBe(true);
  const conversion = await uploadResponse.json();
  const id = conversion.document.id as string;
  createdId = id;
  expect(conversion.document.content_hash).toBe(createHash('sha256').update(readFileSync(pdfPath)).digest('hex'));
  await expect.poll(async () => (await fetchJson(`/api/documents/${id}/conversion`)).status,
    { timeout: 300_000, intervals: [2000] }).toBe('ready');
  await expect(page.locator('button.document-toggle').first()).toBeVisible({ timeout: 15_000 });
  if (await page.locator('button.document-toggle').first().getAttribute('aria-expanded') !== 'true') {
    await page.locator('button.document-toggle').first().click();
  }
  await expect(page.getByRole('button', { name: 'Markdown’ı incele ve yayınla' })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Markdown’ı incele ve yayınla' }).click();
  await expect(page.getByRole('heading', { name: 'Formüller ve kaynak karşılaştırması' })).toBeVisible();
  const ready = await fetchJson(`/api/documents/${id}/conversion`);
  expect(ready.quality_report.formulas.length).toBeGreaterThanOrEqual(2);
  expect(ready.markdown).toContain('\\frac');
  expect(ready.markdown).toContain('\\sqrt');
  for (const formula of ready.quality_report.formulas) {
    expect(formula.validation.status).toMatch(/source_agreement|review/);
    expect(formula.validation.model).toBeTruthy();
  }
  await page.locator('.formula-review summary').first().click();
  await expect(page.locator('.formula-review img').first()).toBeVisible();
  await expect.poll(() => page.locator('.formula-review img').first().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  if (ready.formula_review_required) {
    await expect(page.getByRole('button', { name: 'Markdown’ı onayla ve öğrenme haritasına dönüştür' })).toBeDisabled();
    await page.getByRole('checkbox', { name: /Formül uyarılarını kaynakla inceledim/ }).check();
  }
  await page.getByRole('button', { name: 'Markdown’ı onayla ve öğrenme haritasına dönüştür' }).click();
  await expect(page.getByRole('checkbox', { name: /Yapay zekâ ile arama bağlamını zenginleştir/ })).toBeVisible({ timeout: 180_000 });
  await page.getByRole('checkbox', { name: /Yapay zekâ ile arama bağlamını zenginleştir/ }).check();
  await page.getByRole('button', { name: 'İncelemeyi onayla ve indekslemeye devam et' }).click();
  await expect.poll(async () => (await fetchJson(`/api/documents/${id}/analysis`)).status,
    { timeout: 180_000, intervals: [2000] }).toBe('approved');
  const before = await fetchJson(`/api/documents/${id}/sections`);
  const sourceSnapshot = before.map((s: { id: string; content: string }) => [s.id, s.content]);
  const search = await request.post(`${api}/api/search`, { data: { query: 'Bayes conditional probability posterior likelihood', limit: 5 } });
  expect(search.ok()).toBe(true);
  expect((await search.json()).some((hit: { document_id: string }) => hit.document_id === id)).toBe(true);
  await page.goto('/harita');
  const topics = await fetchJson('/api/topics');
  let formulaStepFound = false;
  for (const topic of topics) {
    const steps = await fetchJson(`/api/topics/${topic.id}/study`);
    if (!steps.some((step: { content: string }) => step.content.includes('$$'))) continue;
    formulaStepFound = true;
    // Default topic selection generally contains the first formula; otherwise choose it.
    if (!(await page.locator('.study-content .katex').count())) {
      const button = page.getByRole('button', { name: topic.title, exact: true });
      if (await button.count()) await button.first().click();
    }
    break;
  }
  expect(formulaStepFound).toBe(true);
  await expect(page.locator('.study-content .katex').first()).toBeVisible({ timeout: 15_000 });
  const progressSaved = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().includes('/progress/'));
  await page.getByRole('button', { name: 'Adımı tamamla', exact: true }).click();
  expect((await progressSaved).ok()).toBe(true);
  await expect(page.getByRole('button', { name: 'Tamamlandı ✓', exact: true })).toBeVisible();
  const explained = page.waitForResponse(response => response.url().endsWith('/api/chat') && response.request().method() === 'POST', { timeout: 180_000 });
  await page.getByRole('button', { name: 'Bu adımı açıkla', exact: true }).click();
  const explanation = await explained;
  expect(explanation.ok()).toBe(true);
  const answer = await explanation.json();
  expect(answer.answer.length).toBeGreaterThan(80);
  expect(answer.sources.length).toBeGreaterThan(0);
  await expect(page.locator('.step-assistant').first()).toBeVisible();
  await page.goto('/kaynaklar');
  await page.locator('button.document-toggle').first().click();
  const setting = page.getByRole('checkbox', { name: /Bu belge için arama bağlamını zenginleştir/ });
  await expect(setting).toBeChecked();
  for (const enabled of [false, true]) {
    await setting.setChecked(enabled);
    await page.getByRole('button', { name: 'Ayarı uygula ve yeniden indeksle' }).click();
    await expect.poll(async () => (await fetchJson(`/api/documents/${id}/index-job`)).status,
      { timeout: 180_000, intervals: [1000] }).toBe('completed');
    await expect(page.getByRole('button', { name: 'Ayarı uygula ve yeniden indeksle' })).toBeEnabled({ timeout: 15_000 });
    expect((await fetchJson('/api/documents')).find((d: {id: string}) => d.id === id).contextual_enrichment).toBe(enabled);
    expect((await fetchJson(`/api/documents/${id}/sections`)).map((s: {id: string; content: string}) => [s.id, s.content])).toEqual(sourceSnapshot);
  }
  await page.reload();
  await page.locator('button.document-toggle').first().click();
  await expect(setting).toBeChecked();
  await page.goto('/harita');
  await expect(page.getByRole('button', { name: 'Tamamlandı ✓', exact: true })).toBeVisible();
  await testInfo.attach('formula-quality-report', { body: JSON.stringify(ready.quality_report, null, 2), contentType: 'application/json' });
  await testInfo.attach('source-section-snapshot', { body: JSON.stringify(sourceSnapshot), contentType: 'application/json' });
  } finally {
    if (createdId) {
      const removed = await request.delete(`${api}/api/documents/${createdId}`);
      expect(removed.status(), 'Yalnız testin oluşturduğu belge temizlenmelidir.').toBe(204);
    }
  }
});
