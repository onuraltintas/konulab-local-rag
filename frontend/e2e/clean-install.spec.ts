import { expect, test } from '@playwright/test';

test('temiz kurulum eski tarayıcı ilerlemesini ve oturumunu geri taşımaz', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('konulab.learning-progress.v1', JSON.stringify({
      version: 4, clientId: 'old-client', topics: { '1': { completedStepIds: ['old-step'], quizAttempts: 9 } },
    }));
    localStorage.setItem('konulab.auth-session.v1', JSON.stringify({ access_token: 'old-token', user: { username: 'old-user' } }));
    localStorage.setItem('konulab.feedback-state.v1', JSON.stringify(['old-feedback']));
  });
  let bootstrapped = false;
  await page.route('http://localhost:8000/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/profiles/bootstrap') {
      const request = route.request().postDataJSON();
      expect(request.client_id).not.toBe('old-client');
      expect(request.local_progress).toEqual({});
      bootstrapped = true;
      return json({ id: '33333333-3333-4333-8333-333333333333', client_id: request.client_id, display_name: 'Yerel öğrenci', progress: {} });
    }
    if (path === '/api/topics' || path === '/api/documents') return json([]);
    if (path === '/api/feedback/state') return json({ keys: [] });
    return json({});
  });
  await page.goto('/kaynaklar');
  await expect(page.getByText('Henüz aktif doküman yok. İlk ders notunu ekleyerek başlayın.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Yerel profili aç' })).toBeVisible();
  await expect.poll(() => bootstrapped).toBe(true);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('konulab.learning-progress.v2') ?? '{}').topics)).toEqual({});
});
