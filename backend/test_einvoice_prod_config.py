"""e-Fatura production yapılandırması — fail-closed ama uygulamayı öldürmeden.

Bu artışın sözleşmesi üç cümlede:

1. **Yapılandırma yoksa uygulama normal açılır.** e-Fatura, ERP'nin geri
   kalanını bloke edemez; SMTP'den farkı budur (SMTP eksikse uygulama bilerek
   açılmaz, çünkü sessizce mailsiz çalışmak daha kötüdür).
2. **Gönderim ucu sessizce başarı taklidi yapmaz.** Eskiden NoOp ``NONE`` döner
   ve 200 gövdesine bir not düşerdi; artık 503 + net Türkçe mesaj gelir ve
   belge durumuna DOKUNULMAZ. 500 hiçbir koşulda yok.
3. **Sinyal ile davranış tek kaynaktan okunur.** ``einvoice_configured`` alanı
   arayüzün butonu gizlemesi içindir ve gönderimin geçtiği kapının aynısına
   bakar; "buton açık ama gönderim reddediliyor" ayrışması testle kapatılır.

Ağ: hiçbir test gerçek sağlayıcıya çıkmaz. Kimlik bilgileri sentetiktir ve
taşıma katmanı patch'lenir.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.einvoice import (
    NoOpEInvoiceProvider,
    einvoice_configuration,
    get_einvoice_provider,
)
from app.einvoice.provider import (
    EINVOICE_CREDENTIALS_MISSING,
    EINVOICE_PROVIDER_UNKNOWN,
    EINVOICE_PROVIDER_UNSET,
)


BACKEND = Path(__file__).resolve().parent

#: Sentetik. Gerçek bir İzibiz hesabı DEĞİLDİR ve olmamalıdır.
FAKE_USERNAME = "SAHTE_KULLANICI"
FAKE_PASSWORD = "SAHTE_PAROLA"
#: İzibiz'in *test* ana makinesi — ortam kilidi (IZIBIZ_ENV=test) yalnız bu
#: imzayı kabul eder. Testler buraya da çıkmaz; taşıma patch'lidir.
SANDBOX_BASE_URL = "https://efaturatest.izibiz.com.tr"


def _settings(**overrides: object) -> SimpleNamespace:
    """Yapılandırılmamış taban; testler yalnız ilgilendikleri alanı değiştirir."""
    base: dict[str, object] = {
        "einvoice_provider": "noop",
        "einvoice_base_url": None,
        "einvoice_username": None,
        "einvoice_password": None,
        "einvoice_api_key": None,
        "einvoice_endpoints_verified": False,
        "izibiz_env": "test",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --- Sinyal: yapılandırma durumu ------------------------------------------
def test_unset_provider_is_not_configured_and_says_which_variable() -> None:
    configuration = einvoice_configuration(_settings())
    assert configuration.configured is False
    assert configuration.provider == "noop"
    assert configuration.reason == EINVOICE_PROVIDER_UNSET


def test_unknown_provider_name_is_not_configured() -> None:
    configuration = einvoice_configuration(_settings(einvoice_provider="bogus"))
    assert configuration.configured is False
    assert configuration.reason == EINVOICE_PROVIDER_UNKNOWN.format(name="bogus")


def test_named_provider_without_credentials_is_not_configured() -> None:
    """Yarım yapılandırma "hazır" değildir; hangi değişkenin eksik olduğu söylenir."""
    configuration = einvoice_configuration(
        _settings(einvoice_provider="izibiz", einvoice_base_url=SANDBOX_BASE_URL)
    )
    assert configuration.configured is False
    assert configuration.reason == EINVOICE_CREDENTIALS_MISSING


def test_credentials_without_base_url_are_still_not_configured() -> None:
    """Kimlik bilgisi tek başına yetmez: uç adresi olmayan adaptör çağrı yapamaz."""
    configuration = einvoice_configuration(
        _settings(
            einvoice_provider="izibiz",
            einvoice_username=FAKE_USERNAME,
            einvoice_password=FAKE_PASSWORD,
        )
    )
    assert configuration.configured is False
    assert configuration.reason


def test_fully_configured_izibiz_reports_configured_without_leaking_secrets() -> None:
    settings = _settings(
        einvoice_provider="izibiz",
        einvoice_base_url=SANDBOX_BASE_URL,
        einvoice_username=FAKE_USERNAME,
        einvoice_password=FAKE_PASSWORD,
    )
    configuration = einvoice_configuration(settings)
    assert configuration.configured is True
    assert configuration.provider == "izibiz"
    assert configuration.reason is None
    # Sinyal bir kimlik bilgisi kanalı değildir.
    rendered = repr(configuration)
    assert FAKE_USERNAME not in rendered
    assert FAKE_PASSWORD not in rendered
    assert SANDBOX_BASE_URL not in rendered


def test_izibiz_rejects_insecure_or_untrusted_endpoint_configuration() -> None:
    for endpoint in (
        SANDBOX_BASE_URL.replace("https://", "http://"),
        SANDBOX_BASE_URL.replace("https://", "https://user:pass@"),
        "https://[bad",
        f"{SANDBOX_BASE_URL}:bad",
        f"{SANDBOX_BASE_URL}:444",
        "https://test.attacker.invalid",
    ):
        configuration = einvoice_configuration(
            _settings(
                einvoice_provider="izibiz",
                einvoice_base_url=endpoint,
                einvoice_username=FAKE_USERNAME,
                einvoice_password=FAKE_PASSWORD,
            )
        )
        assert configuration.configured is False, endpoint


def test_settings_repr_redacts_einvoice_secrets() -> None:
    configured = Settings(
        einvoice_provider="izibiz",
        einvoice_base_url=SANDBOX_BASE_URL,
        einvoice_username=FAKE_USERNAME,
        einvoice_password="PASSSECRET",
        einvoice_api_key="APISECRET",
    )
    rendered = repr(configured)
    assert FAKE_USERNAME not in rendered
    assert "PASSSECRET" not in rendered
    assert "APISECRET" not in rendered
    assert configured.einvoice_password is not None
    assert configured.einvoice_password.get_secret_value() == "PASSSECRET"
    assert configured.einvoice_username is not None
    assert configured.einvoice_username.get_secret_value() == FAKE_USERNAME
    assert configured.einvoice_api_key is not None
    assert configured.einvoice_api_key.get_secret_value() == "APISECRET"
    provider = get_einvoice_provider(configured)
    assert provider.is_configured() is True
    assert provider._username == FAKE_USERNAME
    assert provider._password == "PASSSECRET"
    assert "APISECRET" in provider._secrets


def test_invalid_izibiz_env_closes_the_gate_instead_of_defaulting_to_test() -> None:
    """``IZIBIZ_ENV=`` yarım kalmış bir deploy'dur; sessizce "test" sayılmaz.

    Ortam okunamıyorsa hiçbir çağrı yapılamaz (``_guard``), dolayısıyla sinyal
    de "hazır" diyemez — yoksa arayüz butonu açar ve her deneme hataya düşerdi.
    """
    for value in ("", "prod", "canli"):
        configuration = einvoice_configuration(
            _settings(
                einvoice_provider="izibiz",
                einvoice_base_url=SANDBOX_BASE_URL,
                einvoice_username=FAKE_USERNAME,
                einvoice_password=FAKE_PASSWORD,
                izibiz_env=value,
            )
        )
        assert configuration.configured is False, value
        assert "IZIBIZ_ENV" in (configuration.reason or ""), value


def test_signal_never_disagrees_with_the_resolved_provider() -> None:
    """Sinyal ile fiili sağlayıcı aynı kapıdan geçer — kural kopyası yok."""
    matrix = (
        _settings(),
        _settings(einvoice_provider="izibiz"),
        _settings(einvoice_provider="izibiz", einvoice_username=FAKE_USERNAME),
        _settings(
            einvoice_provider="izibiz",
            einvoice_username=FAKE_USERNAME,
            einvoice_password=FAKE_PASSWORD,
        ),
        _settings(
            einvoice_provider="izibiz",
            einvoice_base_url=SANDBOX_BASE_URL,
            einvoice_username=FAKE_USERNAME,
            einvoice_password=FAKE_PASSWORD,
        ),
        _settings(
            einvoice_provider="nes",
            einvoice_username=FAKE_USERNAME,
            einvoice_password=FAKE_PASSWORD,
        ),
    )
    for settings in matrix:
        provider = get_einvoice_provider(settings)
        assert einvoice_configuration(settings).configured is provider.is_configured()


def test_noop_reports_itself_as_unconfigured() -> None:
    provider = NoOpEInvoiceProvider()
    assert provider.is_configured() is False
    assert provider.configuration_error()


# --- HTTP: tek alt süreçte tam senaryo -------------------------------------
# Motor ve ayarlar import anında bağlandığı için HTTP senaryosu alt süreçte
# çalışır. TEK alt süreç kullanılıyor: her ek süreç migration + admin seed
# maliyetini yeniden ödetiyor ve backend-quality job'ı 15 dakikalık sınırın
# zaten %94'ünde (develop'ta 14dk). Yapılandırılmış hâle, ikinci bir süreç
# yerine ``app.config.settings`` üzerinde geçiliyor — router'ın okuduğu nesne
# tam olarak bu, dolayısıyla kapı gerçek kapı.
_SCENARIO = r"""
import json
from fastapi.testclient import TestClient
from app.config import settings
from app.einvoice import transport as transport_module
from app.einvoice.provider import IzibizEInvoiceProvider
from app.main import app

# Gerçek sağlayıcıya çıkış YASAK: soket açılacak yerde hata fırlatılır.
opened = []
taxpayer_checks = []
submitted_payloads = []

def _refuse(self, method, url, **kwargs):
    opened.append(url)
    raise transport_module.TransportError('test: ag kapali')

transport_module.HttpTransport.request = _refuse

def _known_earsiv(self, vkn):
    taxpayer_checks.append(vkn)
    return {'is_efatura_user':False}

_real_submit = IzibizEInvoiceProvider.submit
def _record_submit(self, payload):
    submitted_payloads.append(payload)
    return _real_submit(self, payload)

IzibizEInvoiceProvider.check_taxpayer = _known_earsiv
IzibizEInvoiceProvider.submit = _record_submit

with TestClient(app) as c:
    # Uygulama e-Fatura yapılandırması OLMADAN açıldı ve hazır bildiriyor.
    assert c.get('/api/ready').status_code == 200

    login = c.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    cid = login['companies'][0]['id']; uid = login['user']['id']
    h = {'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    ch = c.post('/api/auth/change-password', headers=h,
                json={'current_password':'admin123','new_password':'EfaturaProd123!'}).json()
    h['Authorization'] = 'Bearer ' + ch['access_token']
    assert c.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'block','credit_limit_policy':'block','tax_number':'1111111111',
    }).status_code == 200

    cust = c.post('/api/customers', headers=h, json={
        'name':'Efatura Musteri','tax_number':'22222222222',
    }).json()
    mach = c.post('/api/machines', headers=h,
                  json={'customer_id':cust['id'],'brand':'B','model':'M','serial_number':'SN-EF'}).json()
    wo = c.post('/api/work-orders', headers=h,
                json={'machine_id':mach['id'],'customer_id':cust['id'],'technician_id':uid,
                      'actual_hours':'2','labor_rate':'50'}).json()
    for st in ('IN_PROGRESS','COMPLETED'):
        assert c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={'status':st}).status_code == 200

    # Fatura kesimi e-Fatura yapılandırmasından BAĞIMSIZ çalışır.
    inv = c.post('/api/invoices/generate', headers=h, json={'work_order_id':wo['id']})
    assert inv.status_code == 201, inv.text
    iid = inv.json()['id']
    assert c.put(f"/api/customers/{cust['id']}", headers=h, json={
        'name':'Efatura Musteri','tax_number':'33333333333',
    }).status_code == 200
    assert c.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'block','credit_limit_policy':'block','tax_number':None,
    }).status_code == 200

    # --- Yapılandırma YOK -------------------------------------------------
    sub = c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h)
    assert sub.status_code == 503, f'{sub.status_code} {sub.text}'
    assert sub.json()['detail'] == 'E-fatura entegrasyonu yapılandırılmamış'
    # Tekrar denemek de aynı cevabı verir; 500'e dönüşmez.
    assert c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h).status_code == 503

    # Belge durumu dokunulmadan kaldı: olmayan bir gönderim iz bırakmaz.
    state = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert state['einvoice_status'] == 'NONE'
    assert state['einvoice_last_error'] is None
    assert state['einvoice_submitted_at'] is None
    assert state['einvoice_configured'] is False
    assert not opened, opened
    assert not taxpayer_checks, taxpayer_checks
    assert not submitted_payloads, submitted_payloads
    frozen_payload = json.loads(state['einvoice_payload'])
    assert frozen_payload['customer']['vkn_tckn'] == '22222222222'
    frozen_uuid = frozen_payload['uuid']

    # --- Yapılandırma VAR (sentetik) --------------------------------------
    assert c.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'block','credit_limit_policy':'block','tax_number':'1111111111',
    }).status_code == 200
    settings.einvoice_provider = 'izibiz'
    settings.einvoice_base_url = 'https://efaturatest.izibiz.com.tr'
    settings.einvoice_username = 'SAHTE_KULLANICI'
    settings.einvoice_password = 'SAHTE_PAROLA'
    settings.izibiz_env = 'test'

    state = c.get(f'/api/invoices/{iid}/einvoice/status', headers=h).json()
    assert state['einvoice_configured'] is True, state

    sub = c.post(f'/api/invoices/{iid}/einvoice/submit', headers=h)
    # Yapılandırma kapısı geçildi: artık 503 yok. 500 de yok.
    assert sub.status_code == 200, f'{sub.status_code} {sub.text}'
    body = sub.json()
    assert body['einvoice_configured'] is True
    assert body['einvoice_status'] != 'NONE', body
    # Sahte kimlik bilgileri yanıta sızmadı.
    assert 'SAHTE_PAROLA' not in sub.text
    assert 'SAHTE_KULLANICI' not in sub.text
    assert taxpayer_checks == ['22222222222'], taxpayer_checks
    assert len(submitted_payloads) == 1, submitted_payloads
    submitted = submitted_payloads[0]
    assert submitted['customer']['vkn_tckn'] == '22222222222'
    assert submitted['channel'] == 'EARSIV'
    assert submitted['profile_id'] == 'EARSIVFATURA'
    assert submitted['uuid'] == frozen_uuid
    assert opened, body
    assert all(url.startswith('https://efaturatest.izibiz.com.tr/') for url in opened), opened

    persisted = json.loads(body['einvoice_payload'])
    assert persisted['customer']['vkn_tckn'] == '22222222222'
    assert persisted['channel'] == 'EARSIV'
    assert persisted['profile_id'] == 'EARSIVFATURA'

    # Faturaya dondurulmuş alıcı VKN'si yoksa ne mükellef sorgusu ne submit
    # çağrılır; sonradan customers tablosundan veri çekilmez.
    cust2 = c.post('/api/customers', headers=h, json={'name':'VKN Yok Musteri'}).json()
    mach2 = c.post('/api/machines', headers=h, json={
        'customer_id':cust2['id'],'brand':'B','model':'M','serial_number':'SN-EF-2',
    }).json()
    wo2 = c.post('/api/work-orders', headers=h, json={
        'machine_id':mach2['id'],'customer_id':cust2['id'],'technician_id':uid,
        'actual_hours':'1','labor_rate':'25',
    }).json()
    for st in ('IN_PROGRESS','COMPLETED'):
        assert c.patch(f"/api/work-orders/{wo2['id']}/status", headers=h, json={'status':st}).status_code == 200
    before_generate = (len(opened), len(taxpayer_checks), len(submitted_payloads))
    inv2 = c.post('/api/invoices/generate', headers=h, json={'work_order_id':wo2['id']})
    assert inv2.status_code == 201, inv2.text
    assert inv2.json()['einvoice_status'] == 'NONE'
    assert inv2.json()['einvoice_submitted_at'] is None
    assert (len(opened), len(taxpayer_checks), len(submitted_payloads)) == before_generate
    sub2 = c.post(f"/api/invoices/{inv2.json()['id']}/einvoice/submit", headers=h)
    assert sub2.status_code == 200, sub2.text
    assert sub2.json()['einvoice_status'] == 'FAILED'
    assert 'alıcı VKN/TCKN' in sub2.json()['einvoice_last_error']
    assert taxpayer_checks == ['22222222222'], taxpayer_checks
    assert len(submitted_payloads) == 1, submitted_payloads

    print(f'EINVOICE_PROD_CONFIG_OK cagrilar={len(opened)}')
"""


def test_unconfigured_returns_503_and_synthetic_credentials_pass_the_gate(tmp_path: Path) -> None:
    """Tek senaryoda: açılış, 503, dokunulmamış belge, sonra açılan kapı.

    Kimlik bilgisi yokken uygulama açılır, ``/api/ready`` yeşildir ve fatura
    kesilir; gönderim 503 döner, 500 asla. Sentetik kimlik verildiğinde 503
    kalkar — taşıma patch'li olduğu için hiçbir aşamada gerçek sağlayıcıya
    çıkılmaz.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'einvoice-prod-config.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    # Miras alınan bir kabuk yapılandırması senaryonun başlangıcını kirletmesin.
    for key in (
        "EINVOICE_PROVIDER",
        "EINVOICE_BASE_URL",
        "EINVOICE_USERNAME",
        "EINVOICE_PASSWORD",
        "EINVOICE_API_KEY",
        "EINVOICE_ENDPOINTS_VERIFIED",
        "IZIBIZ_ENV",
    ):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-c", _SCENARIO], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "EINVOICE_PROD_CONFIG_OK" in completed.stdout, completed.stdout + completed.stderr
