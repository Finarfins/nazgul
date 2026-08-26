import os, shutil, tempfile, subprocess, time, socket, sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT/'backend'
tmpdir=Path(tempfile.mkdtemp());db=tmpdir/'e2e.db';shutil.copy2(BACKEND/'veriler.db',db)
env=os.environ.copy();env['DATABASE_URL']=f'sqlite:///{db.as_posix()}'
proc=subprocess.Popen(['python','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','5061'],cwd=BACKEND,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)

def wait_port():
    for _ in range(80):
        try:
            with socket.create_connection(('127.0.0.1',5061),timeout=.2): return
        except OSError: time.sleep(.15)
    raise RuntimeError('server başlamadı')

try:
    wait_port()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900})
        page.goto('http://127.0.0.1:5061/giris')
        page.get_by_label('Kullanıcı adı').fill('admin');page.get_by_label('Şifre').fill('admin123');page.get_by_role('button',name='Giriş Yap').click()
        expect(page.get_by_text('İşletme Özeti')).to_be_visible(timeout=10000)
        # müşteri ekle
        page.goto('http://127.0.0.1:5061/musteriler');page.get_by_role('button',name='Yeni Cari').click()
        page.get_by_label('İsim / Ünvan').fill('Tarayıcı Test Müşterisi');page.get_by_label('Telefon').fill('5559998877');page.get_by_role('button',name='Kaydet').click()
        expect(page.get_by_text('Tarayıcı Test Müşterisi')).to_be_visible(timeout=7000)
        # ürün ekle, stok 0
        page.goto('http://127.0.0.1:5061/urunler');page.get_by_role('button',name='Yeni Ürün').click()
        page.get_by_label('Ürün adı').fill('Tarayıcı Test Ürünü');page.get_by_label('Ürün kodu').fill('E2E-NEG');page.get_by_label('Satış fiyatı').fill('125');page.get_by_label('Stok').fill('0');page.get_by_role('button',name='Kaydet').click()
        expect(page.get_by_text('Tarayıcı Test Ürünü')).to_be_visible(timeout=7000)
        # negatif stok satışı
        page.goto('http://127.0.0.1:5061/satislar');page.get_by_role('button',name='Yeni Satış').click()
        customer=page.get_by_label('Müşteri');customer.fill('Tarayıcı Test Müşterisi');page.get_by_role('option',name='Tarayıcı Test Müşterisi').click()
        product=page.get_by_label('Ürün ara');product.fill('Tarayıcı Test Ürünü');page.get_by_role('option').filter(has_text='Tarayıcı Test Ürünü').click()
        page.get_by_label('Miktar').fill('3');page.get_by_role('button',name='Kaydet').click()
        expect(page.get_by_text('Stok yetersiz:')).to_be_visible(timeout=5000)
        page.get_by_role('button',name='Kaydet').click()
        expect(page.get_by_text('Tarayıcı Test Müşterisi')).to_be_visible(timeout=7000)
        # mobil temel görünüm
        mobile=browser.new_page(viewport={'width':390,'height':844})
        mobile.goto('http://127.0.0.1:5061/giris');mobile.get_by_label('Kullanıcı adı').fill('admin');mobile.get_by_label('Şifre').fill('admin123');mobile.get_by_role('button',name='Giriş Yap').click();expect(mobile.get_by_text('İşletme Özeti')).to_be_visible(timeout=10000)
        assert mobile.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2')
        browser.close()
    con=sqlite3.connect(db);stock=con.execute("SELECT stock FROM products WHERE product_code='E2E-NEG'").fetchone()[0];assert stock==-3,stock;assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close()
    print('ALL_NEXT_1_0_1_BROWSER_E2E_TESTS_PASSED')
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except: proc.kill()
