import { expect, test } from '@playwright/test';
import { adminPassword, loginAdmin, loginCustomer, placeSingleOrder, uniqueTableNo } from './helpers';

test.describe('security audit', () => {
  test('admin pages redirect anonymous users to the login page', async ({ page }) => {
    for (const path of ['/admin/kitchen', '/admin/billing', '/admin/menus', '/admin/history']) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/admin\/login$/);
      await expect(page.getByRole('button', { name: '입장하기' })).toBeVisible();
    }
  });

  test('order API rejects malformed and abusive quantities', async ({ page }) => {
    await loginCustomer(page, uniqueTableNo());
    const token = await page.evaluate<string>('csrfToken');

    const rejected = await page.request.post('/api/order', {
      headers: { 'X-CSRF-Token': token },
      data: {
        cart: [
          { name: '펩시제로', quantity: -1 },
          { name: '칠성사이다', quantity: 0 },
          { name: '즉석밥', quantity: 1001 },
          { name: '없는메뉴', quantity: 1, price: -999999 },
        ],
      },
    });

    expect(rejected.status()).toBe(400);
    expect(await rejected.json()).toEqual({ status: 'error', message: '유효한 주문이 없습니다.' });
  });

  test('admin login should throttle repeated wrong passwords', async ({ request }) => {
    const attempts = [];
    for (let i = 0; i < 8; i += 1) {
      attempts.push(await request.post('/admin/login', {
        form: { password: `wrong-${Date.now()}-${i}` },
        maxRedirects: 0,
      }));
    }

    const statusCodes = attempts.map(response => response.status());
    expect(statusCodes, `status codes: ${statusCodes.join(', ')}`).toContain(429);
  });

  test('state-changing admin endpoints should reject tokenless cross-site POSTs', async ({ page }) => {
    await loginAdmin(page);

    let toggled = false;
    try {
      const response = await page.request.post('/admin/toggle_menu/1', {
        headers: {
          Origin: 'https://attacker.example',
          Referer: 'https://attacker.example/csrf.html',
        },
        maxRedirects: 0,
      });
      toggled = response.ok();

      expect(
        [400, 401, 403, 419],
        `tokenless cross-site POST returned ${response.status()}`,
      ).toContain(response.status());
    } finally {
      if (toggled) {
        await page.request.post('/admin/toggle_menu/1');
      }
    }
  });

  test('admin logout should not be reachable with GET', async ({ page }) => {
    await loginAdmin(page);
    const response = await page.request.get('/admin/logout', { maxRedirects: 0 });

    expect(response.status()).toBe(405);
  });

  test('customer login labels should be associated with their inputs', async ({ page }) => {
    await page.goto('/');

    await page.getByLabel('테이블 번호').fill(String(uniqueTableNo()));
    await page.getByLabel('주문자 이름').fill('접근성테스트');
    await page.getByLabel('전화번호').fill('01012345678');
    await page.getByLabel('소속 단체명').fill('개인');

    await expect(page.getByRole('button', { name: '메뉴판 보기' })).toBeEnabled();
  });

  test('multiple customers can intentionally share one table from separate browsers', async ({ browser }) => {
    const tableNo = uniqueTableNo();
    const firstContext = await browser.newContext({ ignoreHTTPSErrors: true });
    const secondContext = await browser.newContext({ ignoreHTTPSErrors: true });

    try {
      const customerA = await firstContext.newPage();
      await loginCustomer(customerA, tableNo, '고객A');
      await placeSingleOrder(customerA, '펩시제로');

      const customerB = await secondContext.newPage();
      await loginCustomer(customerB, tableNo, '고객B');
      await customerB.goto('/my_orders');

      await expect(customerB.getByText('펩시제로')).toBeVisible();
    } finally {
      await firstContext.close();
      await secondContext.close();
    }
  });

  test('default admin password should not work in production-like runs', async ({ page }) => {
    test.skip(adminPassword !== 'defaultadmin', 'ADMIN_PASSWORD is not the Flask code default in this environment.');

    await page.goto('/admin/login');
    await page.getByPlaceholder('비밀번호 입력').fill('defaultadmin');
    await page.getByRole('button', { name: '입장하기' }).click();

    await expect(page).not.toHaveURL(/\/admin\/kitchen$/);
  });
});
