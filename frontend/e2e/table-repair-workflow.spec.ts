import { expect, test } from '@playwright/test';

test('otomatik tablo önerisi yalnız kullanıcı kabulünde kaynağındaki tabloyu değiştirir', async ({ page }) => {
  const documentId = '11111111-1111-4111-8111-111111111111';
  const assetId = '22222222-2222-4222-8222-222222222222';
  let inclusion = 'pending';
  let refined = false;
  const source = '> Özgün tablo görseli esas alınmalıdır.';
  const proposed = '| Ad | Değer |\n| --- | --- |\n| A | 2 |';
  const document = { id: documentId, logical_key: 'table-repair', source_name: 'tablo.pdf', lesson_title: null, media_type: 'application/pdf', content_hash: 'a'.repeat(64), version: 1, status: 'processing', byte_size: 1, created_at: null, replaced_at: null };
  const asset = () => ({ id: assetId, page_number: 8, kind: 'table', url: `/api/documents/${documentId}/assets/${assetId}`, analysis_status: 'ready', description: 'Tablo, A değerini gösterir.', confidence: 90, needs_review: true, inclusion_status: inclusion, structured_markdown: source, row_count: 2, column_count: 2, table_index: 0, table_repair_required: true, proposed_table: { rows: 2, columns: 2, cells: [] }, proposed_markdown: proposed, table_validation: { status: 'manual_review', message: 'Kaynakla tam eşleşme doğrulanamadı; görsel ile karşılaştırarak karar verin.', word_recall: .9, word_precision: 1, missing_numbers: [], additional_numbers: [] } });
  const conversion = () => ({ document, status: 'ready', markdown: `# Ders\n\n<!-- konulab-table-body:0:start -->\n\n${inclusion === 'accepted' ? proposed : source}\n\n<!-- konulab-table-body:0:end -->\n\nSonraki konu.`, markdown_hash: 'b'.repeat(64), converter: 'docling', converter_version: 'test', ocr_used: false, page_count: 1, table_count: 1, image_count: 0, stage: 'review', progress: 100, message: 'Tablo önerisini inceleyin.', error_message: null, assets: [asset()], current_revision_number: 1, revisions: [] });
  await page.route('http://localhost:8000/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/profiles/bootstrap') return json({ id: '33333333-3333-4333-8333-333333333333', client_id: 'table-test', display_name: 'Öğrenci', progress: {} });
    if (path === '/api/documents') return json([document]);
    if (path === '/api/topics') return json([]);
    if (path === `/api/documents/${documentId}/conversion`) {
      const value = conversion();
      return json(refined ? { ...value, assets: [{ ...asset(), proposed_table: { rows: 2, columns: 2, cells: [], refinement: { status: 'manual_review', cells: [{ row: 1, column: 0, before: 'Eşesiz', after: 'Eşeysiz', accepted: true, dpi: 576 }], errors: [], context_added: [], structure_review: { issues: [] } } } }] } : value);
    }
    if (path.endsWith(`/assets/${assetId}/analyze`)) {
      expect(new URL(route.request().url()).searchParams.get('refine_only')).toBe('true');
      refined = true;
      return json(asset());
    }
    if (path.endsWith(`/assets/${assetId}/inclusion/accepted`)) { inclusion = 'accepted'; return json(asset()); }
    if (path.endsWith(`/assets/${assetId}/inclusion/rejected`)) { inclusion = 'rejected'; return json(asset()); }
    if (path.endsWith(`/assets/${assetId}`)) return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF1cAAAAASUVORK5CYII=', 'base64') });
    return json({});
  });
  await page.goto('/kaynaklar');
  await page.locator('button.document-toggle').click();
  await page.getByRole('button', { name: 'Markdown’ı incele ve yayınla' }).click();
  await expect(page.getByRole('heading', { name: 'Görsel modelin tablo önerisi' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Markdown’ı onayla ve öğrenme haritasına dönüştür' })).toBeDisabled();
  await expect(page.locator('.table-repair-proposal')).toContainText(proposed);
  await expect(page.locator('.markdown-preview')).not.toContainText(proposed);
  await page.getByRole('button', { name: 'Şüpheli hücreleri yüksek çözünürlükte kontrol et' }).click();
  await expect(page.getByText('Yüksek çözünürlüklü kontrol · 1 şüpheli okuma incelendi')).toBeVisible();
  await page.getByText('Yüksek çözünürlüklü kontrol · 1 şüpheli okuma incelendi').click();
  await expect(page.getByText(/576 DPI.*Eşesiz → Eşeysiz/)).toBeVisible();
  await expect(page.locator('.markdown-preview')).not.toContainText(proposed);
  await page.getByRole('button', { name: 'Tabloyu inceledim, öneriyi kabul et' }).click();
  await expect(page.locator('.markdown-preview')).toContainText(proposed);
  await expect(page.locator('.markdown-preview')).toContainText('Sonraki konu.');
  await expect(page.getByRole('button', { name: 'Markdown’ı onayla ve öğrenme haritasına dönüştür' })).toBeEnabled();
  await page.getByRole('button', { name: 'Özgün tabloyu koru', exact: true }).click();
  await expect(page.locator('.markdown-preview')).toContainText(source);
  await expect(page.locator('.markdown-preview')).not.toContainText(proposed);
});
