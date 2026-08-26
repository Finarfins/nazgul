import {test, expect, login} from './helpers';

// Oturum akışı: gerçek cookie/CSRF zinciriyle giriş ve panele varış.
// (Bootstrap hesabın zorunlu ilk şifre değişimi global-setup'ta API üzerinden
// tamamlanır; burada değişmiş şifreyle tam UI girişi doğrulanır.)
test('giriş yapılır ve panel konsol-temiz açılır', async ({page}) => {
  await login(page);
  await expect(page.getByText('Ana Sayfa')).toBeVisible();
});

test('yanlış şifre kullanıcıya okunabilir hata gösterir', async ({page}) => {
  await page.goto('/giris');
  await page.getByLabel('Kullanıcı adı').fill('admin');
  await page.getByLabel('Şifre').fill('yanlis-sifre-123');
  await page.getByRole('button', {name: 'Giriş Yap'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page).toHaveURL(/giris/);
});
