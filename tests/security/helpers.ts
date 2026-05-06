import { expect, type Page } from '@playwright/test';

export const adminPassword = process.env.ADMIN_PASSWORD || 'admin_password';

export function uniqueTableNo() {
  return Math.floor(Date.now() / 1000) + Math.floor(Math.random() * 1000);
}

export async function loginCustomer(page: Page, tableNo: number, name = '보안테스트') {
  await page.goto('/');
  await page.locator('#tableNo').fill(String(tableNo));
  await page.locator('#customerName').fill(name);
  await page.locator('#customerPhone').fill('01012345678');
  await page.locator('#organization').fill('개인');
  await expect(page.getByRole('button', { name: '메뉴판 보기' })).toBeEnabled();
  await page.getByRole('button', { name: '메뉴판 보기' }).click();
  await expect(page).toHaveURL(/\/menu$/);
}

export async function loginAdmin(page: Page) {
  await page.goto('/admin/login');
  await page.getByPlaceholder('비밀번호 입력').fill(adminPassword);
  await page.getByRole('button', { name: '입장하기' }).click();
  await expect(page).toHaveURL(/\/admin\/kitchen$/);
}

export async function placeSingleOrder(page: Page, menuName = '펩시제로') {
  await page.getByRole('button', { name: `${menuName} 추가` }).click();
  await page.getByRole('button', { name: /담은 메뉴/ }).click();
  const dialogPromise = page.waitForEvent('dialog');
  await page.getByRole('button', { name: '주문하기' }).click();
  const dialog = await dialogPromise;
  expect(dialog.message()).toContain('주방으로 주문이 전달되었습니다!');
  await dialog.accept();
}
