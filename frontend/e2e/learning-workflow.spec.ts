import { expect, test } from '@playwright/test';

const documentId = '11111111-1111-4111-8111-111111111111';
const sectionId = '22222222-2222-4222-8222-222222222222';
const profileId = '33333333-3333-4333-8333-333333333333';
const topic = {
  id: 1,
  slug: 'test-konusu',
  title: 'Test Konusu',
  description: 'E2E akışı için kaynaklı konu.',
  parent_id: null,
  order_index: 1,
};

test('doküman yükleme, onay, öğrenme, quiz ve silme akışı', async ({ page }) => {
  let documentVisible = false;
  let approved = false;
  const document = {
    id: documentId,
    logical_key: 'e2e-test-notu',
    source_name: 'e2e-test-notu.md',
    lesson_title: 'E2E Test Dersi',
    media_type: 'text/markdown',
    content_hash: 'a'.repeat(64),
    version: 1,
    status: 'processing',
    byte_size: 128,
    created_at: '2026-09-18T00:00:00Z',
    replaced_at: null,
  };
  const readyAnalysis = {
    document,
    model: 'test-model',
    suggested_lesson_title: 'E2E Test Dersi',
    status: 'ready',
    stage: 'ready',
    progress: 100,
    message: 'Konu önerileri incelemeniz için hazır.',
    error_message: null,
    topics: [{ id: '44444444-4444-4444-8444-444444444444', title: topic.title, description: topic.description, existing_topic_id: null, section_count: 1, example_count: 1, code_count: 0, exercise_count: 0 }],
  };

  await page.route('http://localhost:8000/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/profiles/bootstrap') return json({ id: profileId, client_id: 'e2e-client', display_name: 'E2E öğrenci', created_at: null, updated_at: null, progress: {} });
    if (path === '/api/topics') return json([topic]);
    if (path === '/api/documents' && method === 'GET') return json(documentVisible ? [{ ...document, status: approved ? 'active' : 'processing' }] : []);
    if (path === '/api/topics/1/study') return json([{ id: 'step-1', section_ids: [sectionId], source_name: 'E2E Test Dersi', heading: 'Temel kavram', content: 'Öğrenme adımının kaynak metni.', content_type: 'explanation', line_start: 1, line_end: 4 }]);
    if (path === '/api/topics/1/prerequisites') return json([]);
    if (path === '/api/documents/analyze' && method === 'POST') {
      documentVisible = true;
      return json(readyAnalysis, 202);
    }
    if (path === `/api/documents/${documentId}/analysis`) return json(approved ? { ...readyAnalysis, document: { ...document, status: 'active' }, status: 'approved', stage: 'completed', progress: 100, message: 'Konular indekslendi; kaynak öğrenmeye hazır.', topics: [] } : readyAnalysis);
    if (path === `/api/documents/${documentId}/lesson-merge-preview`) return json({ lesson_title: 'E2E Test Dersi', matching_documents: [], overlapping_topics: [] });
    if (path === `/api/documents/${documentId}/approve-analysis` && method === 'POST') {
      approved = true;
      return json({ ...readyAnalysis, document: { ...document, status: 'active' }, status: 'approved', stage: 'completed', progress: 100, message: 'Konular indekslendi; kaynak öğrenmeye hazır.', topics: [] }, 202);
    }
    if (path === '/api/topics/1/quiz' && method === 'POST') return json({ topic_id: 1, questions: [{ id: 'q1', question: 'Test sorusu?', options: [{ id: 'a', text: 'Doğru yanıt' }, { id: 'b', text: 'Yanlış yanıt' }], correct_option_id: 'a', explanation: 'Kaynak adımı doğru yanıtı destekler.', related_step_id: 'step-1' }] });
    if (path === '/api/documents/deletion-impact') return json({ document_ids: [documentId], affected_topics: [topic], learner_progress_count: 1, prerequisite_relation_count: 0, conversation_count: 0, feedback_count: 0 });
    if (path === '/api/documents' && method === 'DELETE') {
      documentVisible = false;
      return json({ deleted_count: 1 });
    }
    if (path.includes(`/profiles/${profileId}/progress/`)) return json({ completed_step_ids: [], review_plans: [], last_step_id: null, last_visited_at: 0, quiz_attempts: 1, last_quiz_score: 1, last_quiz_at: 1 });
    if (path.includes(`/profiles/${profileId}/dashboard`)) return json({ display_name: 'E2E öğrenci', total_topics: 1, completed_topics: 0, in_progress_topics: 0, total_completed_steps: 0, total_steps: 1, today_reviews: [], in_progress: [], recent_quizzes: [] });
    return json({});
  });

  await page.goto('/kaynaklar');
  await page.locator('#source-file').setInputFiles({ name: 'e2e-test-notu.md', mimeType: 'text/markdown', buffer: Buffer.from('# Test Konusu\n\nKaynak metni') });
  await page.getByRole('button', { name: 'Kuyruğu başlat' }).click();
  await expect(page.getByText('1 doküman incelemeye hazır.')).toBeVisible();
  await page.locator('button.document-toggle').click();
  await page.getByRole('button', { name: 'İncele ve onayla' }).click();
  await page.getByRole('button', { name: 'İncelemeyi onayla ve indekslemeye devam et' }).click();
  await expect(page.getByText('Kaynak indekslendi. Öğrenme haritasından konuları açabilir')).toBeVisible();

  await page.goto('/harita');
  await expect(page.getByText('Temel kavram')).toBeVisible();
  await page.getByRole('tab', { name: 'Mini quiz' }).click();
  await page.getByRole('button', { name: 'Mini quiz başlat' }).click();
  await page.locator('.quiz-question').getByRole('button', { name: /Doğru yanıt/ }).click();
  await page.getByRole('button', { name: 'Yanıtları değerlendir' }).click();
  await expect(page.getByText('Harika! Tüm quiz sorularını doğru yanıtladın.')).toBeVisible();

  await page.goto('/kaynaklar');
  await page.locator('button.delete-document').first().click();
  await expect(page.getByText('Etkilenen konular: 1 — Test Konusu')).toBeVisible();
  await page.getByRole('button', { name: 'Evet, kaldır' }).click();
  await expect(page.getByText('Henüz aktif doküman yok. İlk ders notunu ekleyerek başlayın.')).toBeVisible();
});

test('kaynaklı özet aynı özet sekmesinde kalır', async ({ page }) => {
  const summaryConversationId = '55555555-5555-4555-8555-555555555555';
  await page.route('http://localhost:8000/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/profiles/bootstrap') return json({ id: profileId, client_id: 'summary-client', display_name: 'Özet öğrencisi', created_at: null, updated_at: null, progress: {} });
    if (path === '/api/topics') return json([topic]);
    if (path === '/api/topics/1/study') return json([{ id: 'step-1', section_ids: [sectionId], source_name: 'E2E Test Dersi', heading: 'Temel kavram', content: 'Öğrenme adımının kaynak metni.', content_type: 'explanation', line_start: 1, line_end: 4 }]);
    if (path === '/api/topics/1/prerequisites') return json([]);
    if (path === '/api/feedback/state') return json({ keys: [] });
    if (path === '/api/chat' && request.method() === 'POST') {
      return json({
        conversation_id: summaryConversationId,
        answer: 'Bu, seçili konu için kaynaklı özet metnidir.',
        model: 'test-model',
        sources: [{ index: 1, section_id: sectionId, source_name: 'E2E Test Dersi', heading_path: 'Temel kavram', line_start: 1, line_end: 4, score: 0.98 }],
      });
    }
    return json({});
  });

  await page.goto('/harita');
  await page.getByRole('tab', { name: 'Özet' }).click();
  await page.getByRole('button', { name: 'Kaynaklı özet oluştur' }).click();

  await expect(page.getByText('Bu, seçili konu için kaynaklı özet metnidir.')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Özet' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: 'Bu özet üzerinden sohbete devam et' })).toBeVisible();
  await expect(page.locator('.chat-card')).toHaveCount(0);
});
