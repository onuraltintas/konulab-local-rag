import { expect, test } from '@playwright/test';

const documentId = '55555555-5555-4555-8555-555555555555';
const assetId = '66666666-6666-4666-8666-666666666666';
const profileId = '77777777-7777-4777-8777-777777777777';

test('dönüşümden gelen görsel, toplu açıklama ve açık kabul ile Markdown’a eklenir', async ({ page }) => {
  let documentVisible = false;
  let analysisRequested = false;
  let accepted = false;
  const document = {
    id: documentId,
    logical_key: 'gorselli-pdf',
    source_name: 'gorselli-pdf.pdf',
    lesson_title: 'Görselli Test Dersi',
    media_type: 'application/pdf',
    content_hash: 'b'.repeat(64),
    version: 1,
    status: 'processing',
    byte_size: 256,
    created_at: '2026-09-19T00:00:00Z',
    replaced_at: null,
  };
  const asset = (status: 'pending' | 'ready', inclusion: 'pending' | 'accepted') => ({
    id: assetId,
    page_number: 1,
    kind: 'picture',
    description: status === 'ready' ? 'Şema, metindeki geri getirme ve doğrulama akışını özetler.' : null,
    confidence: status === 'ready' ? 91 : null,
    needs_review: false,
    url: `/api/documents/${documentId}/assets/${assetId}`,
    analysis_status: status,
    model: status === 'ready' ? 'qwen3.8:27b' : null,
    message: status === 'ready' ? 'Açıklama hazır.' : null,
    error_message: null,
    structured_markdown: null,
    row_count: null,
    column_count: null,
    inclusion_status: inclusion,
  });
  const conversion = () => ({
    document,
    status: 'ready',
    markdown: accepted
      ? '# Geri getirme\n\n<!-- konulab-asset:' + assetId + ' -->\n\n![Dokümandan çıkarılan görsel](./assets-v1/picture-1.png)\n\n> Görsel açıklaması: Şema, metindeki geri getirme ve doğrulama akışını özetler.'
      : '# Geri getirme\n\n<!-- konulab-asset:' + assetId + ' -->',
    markdown_hash: accepted ? 'c'.repeat(64) : 'd'.repeat(64),
    converter: 'docling',
    converter_version: 'test',
    ocr_used: false,
    page_count: 1,
    table_count: 0,
    image_count: 1,
    stage: 'review',
    progress: 100,
    message: 'Markdown incelemeniz için hazır.',
    error_message: null,
    assets: [asset(analysisRequested ? 'ready' : 'pending', accepted ? 'accepted' : 'pending')],
    current_revision_number: accepted ? 2 : 1,
    revisions: [],
  });

  await page.route('http://localhost:8000/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/profiles/bootstrap') return json({ id: profileId, client_id: 'visual-e2e-client', display_name: 'E2E öğrenci', created_at: null, updated_at: null, progress: {} });
    if (path === '/api/topics') return json([]);
    if (path === '/api/documents' && method === 'GET') return json(documentVisible ? [document] : []);
    if (path === '/api/documents/convert' && method === 'POST') {
      documentVisible = true;
      return json(conversion(), 202);
    }
    if (path === `/api/documents/${documentId}/conversion`) return json(conversion());
    if (path === `/api/documents/${documentId}/assets/analyze-all` && method === 'POST') {
      analysisRequested = true;
      return json({ queued_count: 1, message: 'Görsel açıklaması sıraya alındı.' }, 202);
    }
    if (path === `/api/documents/${documentId}/assets/accept-all` && method === 'POST') {
      accepted = true;
      return json(conversion());
    }
    if (path === `/api/documents/${documentId}/assets/${assetId}`) {
      return route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from([]) });
    }
    if (path.includes(`/profiles/${profileId}/`)) return json({});
    return json({});
  });

  await page.goto('/kaynaklar');
  await page.locator('#source-file').setInputFiles({ name: 'gorselli-pdf.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') });
  await page.getByRole('button', { name: 'Kuyruğu başlat' }).click();
  await expect(page.getByText('1 doküman incelemeye hazır.')).toBeVisible();
  await page.locator('button.document-toggle').click();
  await page.getByRole('button', { name: 'Markdown’ı incele ve yayınla' }).click();
  await expect(page.getByText('Korunan görseller')).toBeVisible();
  await page.getByRole('button', { name: 'Tüm görselleri sırayla açıkla' }).click();
  await expect(page.getByText('Açıklama hazır · 91%')).toBeVisible();
  await page.getByRole('button', { name: 'Hazır açıklamaların tümünü kabul et' }).click();
  await expect(page.locator('.markdown-preview')).toContainText('![Dokümandan çıkarılan görsel](./assets-v1/picture-1.png)');
  await expect(page.locator('.markdown-preview')).toContainText('Görsel açıklaması: Şema, metindeki geri getirme ve doğrulama akışını özetler.');
});
