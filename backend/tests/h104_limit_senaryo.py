"""H104 senaryosu — SQLite testi ve PG ikizi AYNI kurguyu koşar.

Lehçeden bağımsızdır: çağıran bir ``engine`` ve o motora bağlı bir
``TestClient`` verir. Her koşu KENDİ firmasını açar (``kosu`` öneki), yani
paylaşılan bir PG şemasında önceki koşunun satırları toplamlara girmez.

Borç belgeleri iki yoldan yazılır:

* ``bounced_check`` ve ``service_fee`` doğrudan SQL ile: karşılıksız çekin
  API yolu (çekle tahsilat -> karşılıksız) ÖDEMEYİ de yazar ve borçla birlikte
  net 0 eder; P2 probu (keşif §1.6) yalnız borç belgesini ölçer. Karşılıksız
  çekin terslemesi için bugün bir uç YOK; ters çift, servis borcunun
  terslemesiyle aynı şekilde (asıl ``reversed`` + ``posted`` negatif karşı
  kayıt) elle kurulur.
* ``late_fee`` API ile (``/api/finance/late-fees/charges``): şekil kısıtı
  dönem ve anlık görüntü sütunlarını zorunlu kılar; motorun yazdığı belge
  kullanılır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

PAROLA = "H104LimitKapisi!2026x"
KURUS = Decimal("0.01")


def gun(fark: int) -> str:
    from app.business_time import business_today

    return (business_today() + timedelta(days=fark)).isoformat()


def para(deger) -> Decimal:
    return Decimal(str(deger)).quantize(KURUS)


class Kurgu:
    def __init__(self, engine, client, kosu: str) -> None:
        from sqlalchemy import text

        from app.auth import hash_password

        self.engine, self.client, self.kosu = engine, client, kosu
        simdi = datetime.now(timezone.utc)
        with engine.begin() as c:
            self.firma = int(c.execute(text(
                "INSERT INTO companies(name,is_active,created_at,negative_stock_policy,"
                "credit_limit_policy) VALUES (:n,:a,:t,'allow','block') RETURNING id"),
                {"n": f"H104 {kosu}", "a": True, "t": simdi}).scalar_one())
            self.kullanici: dict[str, int] = {}
            for rol in ("admin", "satis"):
                ad = f"h104{rol}{kosu}"
                uid = int(c.execute(text(
                    "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                    "role,is_active,created_at,must_change_password) VALUES "
                    "(:u,:e,:v,:u,:h,:r,:a,:t,:m) RETURNING id"),
                    {"u": ad, "e": f"{ad}@h104.example", "v": True, "h": hash_password(PAROLA),
                     "r": rol, "a": True, "t": simdi, "m": False}).scalar_one())
                c.execute(text(
                    "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                    "VALUES (:u,:c,:d,:t)"), {"u": uid, "c": self.firma, "d": True, "t": simdi})
                self.kullanici[rol] = uid
            self.depo = int(c.execute(text(
                "INSERT INTO warehouses(company_id,name,is_active,is_default) "
                "VALUES (:c,'H104 Depo',:a,:a) RETURNING id"),
                {"c": self.firma, "a": True}).scalar_one())
            self.urun = int(c.execute(text(
                "INSERT INTO products(company_id,name,unit,sale_price,active) "
                "VALUES (:c,'H104 Urun','Adet',1,:a) RETURNING id"),
                {"c": self.firma, "a": True}).scalar_one())
            self.makine = int(c.execute(text(
                "INSERT INTO machines(company_id,brand,model,status,is_active,created_at,updated_at) "
                "VALUES (:c,'H104','M1','active',:a,:t,:t) RETURNING id"),
                {"c": self.firma, "a": True, "t": simdi}).scalar_one())
            c.execute(text(
                "INSERT INTO late_fee_policies(company_id,customer_id,annual_rate,day_count_basis,"
                "grace_days,tax_mode,vat_rate,effective_from,active) VALUES "
                "(:c,NULL,36.5,365,0,'NO_VAT',0,:ef,:a)"),
                {"c": self.firma, "ef": gun(-400), "a": True})
        self.h = {rol: self._giris(rol) for rol in ("admin", "satis")}
        self._sayac = 0

    # --- altyapı ---------------------------------------------------------
    def _giris(self, rol: str) -> dict:
        cevap = self.client.post(
            "/api/auth/login", json={"username": f"h104{rol}{self.kosu}", "password": PAROLA})
        assert cevap.status_code == 200, cevap.text
        self.client.cookies.clear()
        return {"Authorization": "Bearer " + cevap.json()["access_token"],
                "X-Company-ID": str(self.firma)}

    def _no(self) -> str:
        self._sayac += 1
        return f"H104-{self.kosu}-{self._sayac}"

    def sql(self, sorgu: str, **p):
        from sqlalchemy import text

        with self.engine.begin() as c:
            sonuc = c.execute(text(sorgu), p)
            return sonuc.all() if sonuc.returns_rows else []

    def politika(self, kip: str) -> None:
        self.sql("UPDATE companies SET credit_limit_policy=:k WHERE id=:c", k=kip, c=self.firma)

    # --- cari ve borç belgeleri -----------------------------------------
    def musteri(self, ad: str, limit: str) -> int:
        return int(self.sql(
            "INSERT INTO customers(company_id,name,opening_balance,risk_limit) "
            "VALUES (:c,:n,0,:l) RETURNING id", c=self.firma, n=f"{ad} {self.kosu}", l=limit)[0][0])

    def _belge(self, mus: int, tur: str, tutar: str, *, durum: str = "posted",
               ters_of: int | None = None, kaynak: dict) -> int:
        kolon, deger = next(iter(kaynak.items()))
        return int(self.sql(
            f"INSERT INTO receivable_charge_documents(company_id,{kolon},customer_id,charge_type,"
            "period_start,period_end,due_date_snapshot,calculation_snapshot,gross_amount,status,"
            "calculation_fingerprint,revision_no,reversal_of_document_id,currency,exchange_rate,"
            "created_by,posted_by,created_at,posted_at) VALUES "
            "(:c,:k,:m,:tur,:ps,:pe,:vade,'{}',:g,:d,:fp,:rev,:ters,'TRY',1,:u,:u,:t,:t) RETURNING id",
            c=self.firma, k=deger, m=mus, tur=tur, ps=gun(-3), pe=gun(-3), vade=gun(-10),
            g=tutar, d=durum, fp=uuid4().hex, rev=2 if ters_of else 1, ters=ters_of,
            u=self.kullanici["admin"], t=datetime.now(timezone.utc))[0][0])

    def karsiliksiz(self, mus: int, tutar: str, *, terslenmis: bool = False) -> int:
        cek = int(self.sql(
            "INSERT INTO cek_senetler(company_id,tur,yon,portfoy_durumu,customer_id,tutar,vade,"
            "seri_no,created_at) VALUES (:c,'cek','alinan','karsiliksiz',:m,:tutar,:v,:s,:t) RETURNING id",
            c=self.firma, m=mus, tutar=tutar, v=gun(-10), s=self._no(),
            t=datetime.now(timezone.utc))[0][0])
        asil = self._belge(mus, "bounced_check", tutar,
                           durum="reversed" if terslenmis else "posted",
                           kaynak={"cek_senet_id": cek})
        if terslenmis:
            self._belge(mus, "bounced_check", f"-{tutar}", ters_of=asil, kaynak={"cek_senet_id": cek})
        return asil

    def servis_borcu(self, mus: int, tutar: str) -> int:
        is_emri = int(self.sql(
            "INSERT INTO work_orders(company_id,machine_id,customer_id,technician_id,work_order_no,"
            "opened_at,created_by) VALUES (:c,:mk,:m,:u,:no,:t,:u) RETURNING id",
            c=self.firma, mk=self.makine, m=mus, u=self.kullanici["admin"], no=self._no(),
            t=datetime.now(timezone.utc))[0][0])
        return self._belge(mus, "service_fee", tutar, kaynak={"work_order_id": is_emri})

    def vade_farki(self, mus: int) -> Decimal:
        """1000 TL veresiye (vade -310) + 300 günlük vade farkı + 1000 TL tahsilat.

        Satış anaparası tahsilatla kapanır, cari bakiyede yalnız vade farkı kalır.
        """
        siparis = int(self.sql(
            "INSERT INTO orders(customer_id,order_date,due_date,final_total,status,paid_amount,"
            "payment_method,payment_term,document_no,company_id) VALUES "
            "(:m,:od,:dd,1000,'completed',0,'credit','HARMAN_VADELI',:no,:c) RETURNING id",
            m=mus, od=gun(-320), dd=gun(-310), no=self._no(), c=self.firma)[0][0])
        h = self.h["admin"]
        taslak = self.client.post(
            "/api/finance/late-fees/charges", headers={**h, "Idempotency-Key": uuid4().hex},
            json={"order_id": siparis, "period_start": gun(-309), "period_end": gun(-10)})
        assert taslak.status_code == 200, taslak.text
        onay = self.client.post(
            f"/api/finance/late-fees/charges/{taslak.json()['id']}/post",
            headers={**h, "Idempotency-Key": uuid4().hex})
        assert onay.status_code == 200, onay.text
        self.tahsilat(mus, "1000.00")
        return para(onay.json()["gross_amount"])

    def tahsilat(self, mus: int, tutar: str) -> None:
        cevap = self.client.post(
            "/api/payments", headers={**self.h["admin"], "Idempotency-Key": uuid4().hex},
            json={"entity_type": "customer", "entity_id": mus, "amount": tutar,
                  "payment_date": gun(-5), "payment_method": "cash"})
        assert cevap.status_code in (200, 201), cevap.text

    # --- ölçüm -------------------------------------------------------------
    def satis(self, mus: int, tutar: str, *, rol: str = "admin", onay: str | None = None):
        h = dict(self.h[rol])
        if onay is not None:
            h.update({"X-Policy-Override": "1", "X-Policy-Override-Reason": onay})
        return self.client.post("/api/orders", headers=h, json={
            "entity_id": mus, "transaction_date": gun(0), "due_date": gun(30),
            "warehouse_id": self.depo, "payment_method": "credit", "document_no": self._no(),
            "items": [{"product_id": self.urun, "quantity": "1", "unit_price": tutar,
                       "vat_rate": 0}]})

    def kart_bakiyesi(self, mus: int) -> Decimal:
        cevap = self.client.get(f"/api/customers/{mus}", headers=self.h["admin"])
        assert cevap.status_code == 200, cevap.text
        return para(cevap.json()["summary"]["current_balance"])

    def kapi_bakiyesi(self, mus: int) -> Decimal:
        from sqlalchemy.orm import Session

        from app.routers.transactions import _credit_exposure

        with Session(self.engine) as db:
            _limit, guncel, _yansitilan = _credit_exposure(
                db, cid=self.firma, customer_id=mus, final_total=Decimal("0"),
                paid_amount=Decimal("0"), status="completed", transaction_id=None)
            db.rollback()
        return para(guncel)

    def eski_kart_formulu(self, mus: int) -> Decimal:
        """Develop ``9a6d7e3``teki kart sorgusunun borç belgesi yüklemi, HARFİ HARFİNE.

        Kartın sayısı değişmedi mi sorusunun kâhini: paylaşılan yardımcıya
        geçişten önceki metin buraya kopyalandı.
        """
        from app.business_time import business_today

        satir = self.sql(
            """SELECT
            (SELECT COALESCE(opening_balance,0) FROM customers WHERE id=:m AND company_id=:c),
            (SELECT COALESCE(SUM(final_total),0) FROM orders WHERE company_id=:c AND customer_id=:m
               AND COALESCE(status,'completed') NOT IN ('draft','cancelled')),
            (SELECT COALESCE(SUM(d.gross_amount),0) FROM receivable_charge_documents d
               WHERE d.company_id=:c AND d.customer_id=:m
                 AND d.charge_type IN ('late_fee','service_fee','bounced_check')
                 AND d.status IN ('posted','reversed')
                 AND d.posted_at IS NOT NULL
                 AND d.period_end<=:as_of),
            (SELECT COALESCE(SUM(amount),0) FROM payments WHERE company_id=:c
               AND entity_type='customer' AND entity_id=:m)""",
            m=mus, c=self.firma, as_of=business_today())[0]
        acilis, satis, borc, odeme = (para(x) for x in satir)
        return acilis + satis + borc - odeme


# --- senaryolar: SQLite testi ve PG ikizi AYNEN çağırır ----------------------
RISK_MESAJI = "Müşteri risk limiti aşılıyor"


def p2_karsiliksiz_cek(k: Kurgu) -> None:
    """Keşif §1.6 P2: limit 1000, 500 TL karşılıksız çek, 600 TL veresiye -> 409."""
    mus = k.musteri("P2", "1000")
    k.karsiliksiz(mus, "500.00")
    assert k.kart_bakiyesi(mus) == Decimal("500.00")
    cevap = k.satis(mus, "600.00")
    assert cevap.status_code == 409, cevap.text
    assert RISK_MESAJI in cevap.json()["detail"], cevap.text
    assert "işlem sonrası bakiye: 1100.00" in cevap.json()["detail"], cevap.text
    assert k.kapi_bakiyesi(mus) == Decimal("500.00")


def terslenmis_karsiliksiz_cek(k: Kurgu) -> None:
    """Asıl ``reversed`` +500 ve karşı kayıt -500 net 0: satış geçer."""
    mus = k.musteri("Ters", "1000")
    k.karsiliksiz(mus, "500.00", terslenmis=True)
    assert k.kart_bakiyesi(mus) == k.kapi_bakiyesi(mus) == Decimal("0.00")
    cevap = k.satis(mus, "600.00")
    assert cevap.status_code == 201, cevap.text


def vade_farki(k: Kurgu) -> None:
    """Deftere işlenmiş vade farkı (~300 TL) + 800 TL veresiye, limit 1000 -> 409."""
    mus = k.musteri("VF", "1000")
    ucret = k.vade_farki(mus)
    assert Decimal("290") <= ucret <= Decimal("310"), ucret
    assert k.kart_bakiyesi(mus) == ucret
    cevap = k.satis(mus, "800.00")
    assert cevap.status_code == 409, cevap.text
    assert f"işlem sonrası bakiye: {ucret + Decimal('800.00'):.2f}" in cevap.json()["detail"]
    assert k.kapi_bakiyesi(mus) == ucret


def yonetici_onayi(k: Kurgu) -> None:
    """``manager_override``: satış rolü 403, admin 201 + ``policy_override_logs`` satırı."""
    k.politika("manager_override")
    try:
        mus = k.musteri("Onay", "1000")
        k.karsiliksiz(mus, "500.00")
        onaysiz = k.satis(mus, "600.00")
        assert onaysiz.status_code == 409, onaysiz.text
        assert "Yönetici onayıyla" in onaysiz.json()["detail"]
        satisci = k.satis(mus, "600.00", rol="satis", onay="musteri garanti verdi")
        assert satisci.status_code == 403, satisci.text
        once = k.sql("SELECT COUNT(*) FROM policy_override_logs WHERE company_id=:c", c=k.firma)[0][0]
        admin = k.satis(mus, "600.00", onay="musteri garanti verdi")
        assert admin.status_code == 201, admin.text
        kayit = k.sql(
            "SELECT policy_name,resource_id,reason,user_id FROM policy_override_logs "
            "WHERE company_id=:c ORDER BY id", c=k.firma)
        assert len(kayit) == once + 1
        assert tuple(kayit[-1]) == ("credit_limit", admin.json()["id"], "musteri garanti verdi",
                                    k.kullanici["admin"])
    finally:
        k.politika("block")


def kapi_kart_esitligi(k: Kurgu) -> None:
    """Üç tür + ters çift + satış + tahsilat: kapı == kart == eski kart formülü."""
    mus = k.musteri("Hepsi", "0")
    ucret = k.vade_farki(mus)
    k.servis_borcu(mus, "150.00")
    k.karsiliksiz(mus, "500.00")
    k.karsiliksiz(mus, "200.00", terslenmis=True)
    beklenen = ucret + Decimal("650.00")
    turler = {r[0] for r in k.sql(
        "SELECT DISTINCT charge_type FROM receivable_charge_documents WHERE company_id=:c "
        "AND customer_id=:m", c=k.firma, m=mus)}
    assert turler == {"late_fee", "service_fee", "bounced_check"}
    assert k.kart_bakiyesi(mus) == beklenen
    assert k.eski_kart_formulu(mus) == beklenen
    assert k.kapi_bakiyesi(mus) == beklenen
