import { expect, test, type Page } from '@playwright/test';

const smokePassword = process.env.DSA_WEB_SMOKE_PASSWORD;

type AuthStatusResponse = {
  authEnabled: boolean;
  loggedIn: boolean;
};

function getPathname(page: Page): string {
  return new URL(page.url()).pathname;
}

async function fetchAuthStatus(page: Page): Promise<AuthStatusResponse | null> {
  try {
    const response = await page.request.get('/api/v1/auth/status', {
      failOnStatusCode: false,
    });
    if (!response.ok()) {
      return null;
    }
    const data = await response.json() as Partial<AuthStatusResponse>;
    if (typeof data.authEnabled !== 'boolean' || typeof data.loggedIn !== 'boolean') {
      return null;
    }
    return {
      authEnabled: data.authEnabled,
      loggedIn: data.loggedIn,
    };
  } catch {
    return null;
  }
}

async function expectShellReady(page: Page) {
  await expect(page.locator('[data-testid="home-dashboard"]')).toBeVisible({ timeout: 10_000 });
}

async function login(page: Page) {
  const authStatus = await fetchAuthStatus(page);

  if (authStatus && (!authStatus.authEnabled || authStatus.loggedIn)) {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await expectShellReady(page);
    return;
  }

  test.skip(!smokePassword, 'Set DSA_WEB_SMOKE_PASSWORD to run authenticated smoke tests.');

  await page.goto('/login');
  await page.waitForLoadState('domcontentloaded');

  if (getPathname(page) !== '/login') {
    await expectShellReady(page);
    return;
  }

  const passwordInput = page.locator('#password');
  const submitButton = page.locator('button[type="submit"]').first();

  await expect(passwordInput).toBeVisible({ timeout: 10_000 });
  await passwordInput.fill(smokePassword!);
  await expect(submitButton).toBeVisible();

  await Promise.all([
    page.waitForResponse(
      (response) => response.url().includes('/api/v1/auth/login') && response.status() < 500,
      { timeout: 15_000 }
    ),
    submitButton.click(),
  ]);

  await page.waitForURL((url) => url.pathname === '/', { timeout: 15_000 });
  await page.waitForLoadState('domcontentloaded');
  await expectShellReady(page);
}

test.describe('web smoke', () => {
  test('login page renders password form or redirects to home', async ({ page }) => {
    await page.goto('/login');
    await page.waitForLoadState('domcontentloaded');

    const passwordInput = page.locator('#password');
    const hasPasswordInput = await passwordInput.isVisible({ timeout: 2_000 }).catch(() => false);

    if (getPathname(page) === '/login' && hasPasswordInput) {
      await expect(passwordInput).toBeVisible();
      await expect(page.locator('button[type="submit"]').first()).toBeVisible();
      return;
    }

    await page.waitForURL((url) => url.pathname === '/', { timeout: 10_000 }).catch(() => undefined);
    await expectShellReady(page);
  });

  test('home page shows analysis entry and history panel after login', async ({ page }) => {
    await login(page);

    await expect(page.locator('[data-testid="home-dashboard"]')).toBeVisible({ timeout: 10_000 });
    const stockInput = page.locator('[data-testid="home-dashboard"] [role="combobox"]').first();
    await expect(stockInput).toBeVisible();

    await expect(page.locator('a[href="/"]').first()).toBeVisible();
    await expect(page.locator('a[href="/chat"]').first()).toBeVisible();

    await stockInput.fill('600519');
    const analyzeButton = page.locator('[data-testid="home-dashboard"] header button.btn-primary').first();
    await expect(analyzeButton).toBeVisible();
    await expect(analyzeButton).toBeEnabled();
  });

  test('chat page allows entering a question and starts a request', async ({ page }) => {
    await login(page);

    await page.goto('/chat');
    await expect(page).toHaveURL(/\/chat$/);
    await page.waitForLoadState('domcontentloaded');

    await expect(page.getByTestId('chat-workspace')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('chat-session-list-scroll')).toBeVisible();
    await expect(page.getByTestId('chat-message-scroll')).toBeVisible();

    const input = page.locator('[data-testid="chat-workspace"] textarea').first();
    await expect(input).toBeVisible({ timeout: 5000 });

    const prompt = 'analyze 600519 briefly';
    await input.fill(prompt);
    await page.locator('[data-testid="chat-workspace"] button.btn-primary').first().click();

    await expect(page.getByText(prompt).last()).toBeVisible({ timeout: 5000 });
  });

  test('chat page uses accessible labels instead of native title attributes for key actions', async ({ page }) => {
    await login(page);

    await page.goto('/chat');
    await expect(page).toHaveURL(/\/chat$/);
    await page.waitForLoadState('domcontentloaded');

    const sendButton = page.locator('[data-testid="chat-workspace"] button.btn-primary').first();
    const composer = page.locator('[data-testid="chat-workspace"] textarea').first();

    await expect(page.getByTestId('chat-workspace')).toBeVisible({ timeout: 10_000 });
    await expect(sendButton).toBeVisible({ timeout: 10_000 });
    await expect(composer).toBeVisible({ timeout: 10_000 });

    await expect(sendButton).not.toHaveAttribute('title', /.+/);
    await expect(composer).not.toHaveAttribute('title', /.+/);
  });

  test('mobile shell opens navigation drawer after login', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);

    const menuButton = page.locator('div.pointer-events-none.fixed.inset-x-0.top-3 button').first();
    await expect(menuButton).toBeVisible({ timeout: 5000 });
    await menuButton.click();

    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible({ timeout: 5000 });
    await expect(drawer.locator('a[href="/backtest"]')).toBeVisible({ timeout: 5000 });
  });

  test('settings page renders title and save actions after login', async ({ page }) => {
    await login(page);

    await page.goto('/settings');
    await expect(page).toHaveURL(/\/settings$/);
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('.settings-page')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('button[data-variant="settings-secondary"]').first()).toBeVisible();
    await expect(page.locator('button[data-variant="settings-primary"]').first()).toBeVisible();
  });

  test('backtest page renders filter controls after login', async ({ page }) => {
    await login(page);

    await page.goto('/backtest');
    await expect(page).toHaveURL(/\/backtest$/);
    await page.waitForLoadState('domcontentloaded');

    const filterInput = page.locator('input[placeholder*="stock code"]').first();
    await expect(filterInput).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /filter/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /run backtest/i })).toBeVisible();
  });
});
