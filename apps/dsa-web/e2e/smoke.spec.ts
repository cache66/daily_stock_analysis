import { expect, test, type Page } from '@playwright/test';

const smokePassword = process.env.DSA_WEB_SMOKE_PASSWORD;

if (!smokePassword) {
  test.skip(true, 'Set DSA_WEB_SMOKE_PASSWORD to run authenticated smoke tests.');
}


async function captureSmokeScreenshot(page: Page, testInfo: { outputPath: (name: string) => string }, name: string, options: { fullPage?: boolean } = {}) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({
    path,
    fullPage: options.fullPage ?? true,
  });
  await testInfo.attach(name, {
    path,
    contentType: 'image/png',
  });
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
  test.use({ locale: 'zh-CN' });

  test('login page renders password form', async ({ page }, testInfo) => {
    await page.goto('/login');
    await page.waitForLoadState('domcontentloaded');

    const passwordInput = page.locator('#password');
    const hasPasswordInput = await passwordInput.isVisible({ timeout: 2_000 }).catch(() => false);

    if (getPathname(page) === '/login' && hasPasswordInput) {
      await expect(passwordInput).toBeVisible();
      await expect(page.locator('button[type="submit"]').first()).toBeVisible();
      return;
    }

    // Check for submit button
    await expect(page.getByRole('button', { name: /授权进入工作台|完成设置并登录/ })).toBeVisible();

    await captureSmokeScreenshot(page, testInfo, 'smoke-login-page-zh');
  });

  test('home page shows analysis entry and history panel after login', async ({ page }, testInfo) => {
    await login(page);

    await expect(page.locator('[data-testid="home-dashboard"]')).toBeVisible({ timeout: 10_000 });
    const stockInput = page.locator('[data-testid="home-dashboard"] [role="combobox"]').first();
    await expect(stockInput).toBeVisible();

    await expect(page.locator('a[href="/"]').first()).toBeVisible();
    await expect(page.locator('a[href="/chat"]').first()).toBeVisible();

    await stockInput.fill('600519');
    const analyzeButton = page.locator('[data-testid="home-dashboard"] header button.btn-primary').first();
    await expect(analyzeButton).toBeVisible();

    await captureSmokeScreenshot(page, testInfo, 'smoke-home-page-zh', { fullPage: true });
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

  test('mobile shell opens navigation drawer after login', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);

    const menuButton = page.locator('div.pointer-events-none.fixed.inset-x-0.top-3 button').first();
    await expect(menuButton).toBeVisible({ timeout: 5000 });
    await menuButton.click();

    // Check if navigation is visible
    await expect(page.getByRole('link', { name: '回测' })).toBeVisible({ timeout: 5000 });

    await captureSmokeScreenshot(page, testInfo, 'smoke-mobile-shell-nav');
  });

  test('settings page renders title and save actions after login', async ({ page }, testInfo) => {
    await login(page);

    await page.goto('/settings');
    await expect(page).toHaveURL(/\/settings$/);
    await page.waitForLoadState('domcontentloaded');

    // Use heading role for more precise selection
    await expect(page.getByRole('heading', { name: '系统设置' })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: '重置' })).toBeVisible();
    await expect(page.getByRole('button', { name: /保存配置/ })).toBeVisible();

    await captureSmokeScreenshot(page, testInfo, 'smoke-settings-page-zh');
  });

  test('language switch updates UI copy and persists after page refresh', async ({ page }, testInfo) => {
    await login(page);

    const languageToggle = page.getByRole('button', { name: '切换界面语言' });
    await expect(languageToggle).toBeVisible();
    await expect(page.getByRole('link', { name: '设置' })).toBeVisible();
    await expect(page.getByRole('link', { name: '首页' })).toBeVisible();

    await languageToggle.click();

    const englishLanguageToggle = page.getByRole('button', { name: 'Switch UI language' });
    await expect(englishLanguageToggle).toBeVisible();
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Home' })).toBeVisible();
    await captureSmokeScreenshot(page, testInfo, 'smoke-home-page-en');

    expect(await page.evaluate(() => localStorage.getItem('dsa.uiLanguage'))).toBe('en');

    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    await expect(englishLanguageToggle).toBeVisible();
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Home' })).toBeVisible();

    await page.getByRole('link', { name: 'Settings' }).click();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);

    await expect(page.getByRole('heading', { name: 'System settings' })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: 'Send test' })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Title' })).toHaveValue('DSA notification test');

    await captureSmokeScreenshot(page, testInfo, 'smoke-settings-page-en');
  });

  test('backtest page renders filter controls after login', async ({ page }, testInfo) => {
    await login(page);

    await page.goto('/backtest');
    await expect(page).toHaveURL(/\/backtest$/);
    await page.waitForLoadState('domcontentloaded');

    // Check for filter controls
    const filterInput = page.getByPlaceholder('按股票代码筛选（留空表示全部）');
    await expect(filterInput).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: '筛选' })).toBeVisible();
    await expect(page.getByRole('button', { name: '运行回测' })).toBeVisible();

    await captureSmokeScreenshot(page, testInfo, 'smoke-backtest-page-zh', { fullPage: true });
  });
});
