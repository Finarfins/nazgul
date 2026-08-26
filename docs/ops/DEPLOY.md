## Sürüm aktarımı — dizin takası

Sunucuya kod `git pull` ile gelmez, dizin `git archive` tarball'u ile takas
edilir. Aşağıdaki adımlar **bu kuruluma** (`mobil-erp`, `harmanzamani.com`,
`/opt/harman-zamani`, `<SUNUCU-IP>`) aittir ve 2026-08-16 deploy'unda
uygulanmıştır.

### `--build` neden yasak

`docker compose up -d --build` imajı **üretim kutusunda derler**. 2026-08-05
kesintisinin nedeni tam olarak budur: derleme tek makinenin CPU ve belleğini
tüketip kutuyu takasa düşürdü, çalışan uygulama 13–15 saniyelik zaman aşımına
girdi ve site yanıt veremez hâle geldi. Bu belgenin varlık sebebi de odur:
**imaj bu kutuda DERLENMEZ**, CI'da derlenir, buraya yalnız GHCR'den immutable
SHA ile indirilir. Sunucuda derleyen her prosedür bu kuralı çiğner.

### Adımlar

`<SHA>` her yerde aynı 40 haneli commit SHA'sıdır — imajın SHA'sı.

**Bloktaki her komut durmaya bağlıdır.** Doğrulayıp devam eden bir adım
yoktur ve olmamalıdır: bildirip geçen bir kontrol, kontrol değildir. Yeni
bir adım eklenirse o da `|| { echo …; exit 1; }` ile bağlanır — bu kural
`deploy/ci-surum-aktarimi-kapisi.py` tarafından ölçülür, dolayısıyla
bağlanmamış bir adım CI'da kırmızıdır.

```bash
# 0) yerelde: tarball'u tam SHA'dan üret, sağlamasını AL (göz için değil,
#    1. adımda makineye verilecek değer olarak)
git archive --format=tar.gz -o release-<SHA>.tar.gz <SHA> \
  || { echo "git archive başarısız — SHA yanlış olabilir"; exit 1; }
YEREL_SHA=$(sha256sum release-<SHA>.tar.gz | cut -d' ' -f1) \
  || { echo "tarball sağlaması alınamadı — DUR"; exit 1; }
echo "$YEREL_SHA"
scp -i ~/.ssh/<ANAHTAR>.pem release-<SHA>.tar.gz <KULLANICI>@<SUNUCU-IP>:/tmp/ \
  || { echo "aktarım başarısız — DUR"; exit 1; }

# 1) sunucuda: aktarımın bütünlüğü. Sağlamayı EKRANA BASIP göz kararı
#    karşılaştırmak bir kontrol değildir; karşılaştırmayı makine yapar.
echo "<YEREL-SHA256>  /tmp/release-<SHA>.tar.gz" | sha256sum -c - \
  || { echo "tarball aktarımda bozuldu — DUR"; exit 1; }
gzip -t /tmp/release-<SHA>.tar.gz \
  || { echo "gzip akışı bozuk — DUR"; exit 1; }

# 2) .env.production'ı release dizini DIŞINA yedekle — git'te olmayan tek dosya
TS=$(date -u +%Y%m%d-%H%M%SZ) \
  || { echo "zaman damgası üretilemedi — yedek dizini damgasız kalır, DUR"; exit 1; }
sudo cp -p /opt/harman-zamani/.env.production /home/ubuntu/backups/env.production.yedek-$TS \
  || { echo ".env yedeği alınamadı — DUR"; exit 1; }
sudo cmp /opt/harman-zamani/.env.production /home/ubuntu/backups/env.production.yedek-$TS \
  || { echo ".env yedeği kaynakla aynı değil — DUR"; exit 1; }

# 3) yeni ağacı yan dizine aç
sudo rm -rf /opt/harman-zamani-yeni && sudo mkdir -p /opt/harman-zamani-yeni \
  || { echo "yan dizin hazırlanamadı — DUR"; exit 1; }
sudo tar -xzf /tmp/release-<SHA>.tar.gz -C /opt/harman-zamani-yeni \
  || { echo "tarball açılamadı — DUR"; exit 1; }

# 4) ağacın EKSİKSİZ olduğunu doğrula — RELEASE_SHA tek başına yetmez.
sudo grep -qx '<SHA>' /opt/harman-zamani-yeni/deploy/RELEASE_SHA \
  || { echo "RELEASE_SHA <SHA> değil — DUR"; exit 1; }
for f in docker-compose.yml docker-compose.prod.yml Dockerfile \
         deploy/sunucu-deploy.sh deploy/Caddyfile; do
  sudo test -e "/opt/harman-zamani-yeni/$f" \
    || { echo "EKSİK: $f — takas YAPILMAZ"; exit 1; }
done

# 5) .env.production'ı yeni ağaca taşı ve özdeşliğini doğrula
sudo cp -p /opt/harman-zamani/.env.production /opt/harman-zamani-yeni/.env.production \
  || { echo ".env yeni ağaca kopyalanamadı — DUR"; exit 1; }
sudo cmp /opt/harman-zamani/.env.production /opt/harman-zamani-yeni/.env.production \
  || { echo ".env.production kopyası farklı — takas YAPILMAZ"; exit 1; }

# 6) TAKAS — eski dizin adıyla saklanır, SİLİNMEZ
sudo mv /opt/harman-zamani /opt/harman-zamani-onceki-$TS \
  || { echo "eski dizin yedeklenemedi — takas YAPILMADI"; exit 1; }
sudo mv /opt/harman-zamani-yeni /opt/harman-zamani \
  || { echo "yeni dizin yerine konamadı — GERİ AL: sudo mv /opt/harman-zamani-onceki-$TS /opt/harman-zamani"; exit 1; }
sudo cmp /opt/harman-zamani/.env.production /opt/harman-zamani-onceki-$TS/.env.production \
  || { echo "takas sonrası .env FARKLI — bu bölümdeki 'Takas ile deploy arasında bir şey giderse' adımlarını uygulayın"; exit 1; }

# 7) deploy
cd /opt/harman-zamani && sudo ./deploy/sunucu-deploy.sh <SHA> \
  || { echo "deploy durdu — betiğin yazdırdığı adımı okuyun, improvize etmeyin"; exit 1; }
```

### Takas ile deploy arasında bir şey giderse

Geri alma, takası tersine çevirmektir. Komutları **takastan önce** yazın.

**TUTULAN ÖZELLİK: hangi adım başarısız olursa olsun İKİ AĞAÇ DA DURUR ve
elle kurtarılabilir.** Hiçbir adım canlı dizini SİLMEZ; geri alma yalnız
yeniden adlandırmayla yapılır. Doğrulama adımları da bu özelliğe tabidir:
**duran bir doğrulama meşrudur, silen bir doğrulama değildir.**

`rm -rf` ile başlayan bir geri alma bu özelliği tutamaz: tam da bir şeyin
ters gittiği anda, kurtarmaya çalıştığı şeyi yok eder. `exit 1` bunu görünür
yapar ama **geri getirmez** — hata denetimi yıkıcı sıralamayı onarmaz.

Geri alınan ağacın **kimliği** de doğrulanır. Bir dosyanın VAR OLMASI
yetmez: içinde `RELEASE_SHA` bulunan herhangi bir ağaç o kontrolü geçer.
Beklenen sürüm, geri alma anında kimsenin ezberden bilmesi gereken bir şey
değildir — **korunan ağacın kendisi taşır**. Bu yüzden 1. adım onu
TAŞIMADAN ÖNCE okur ve 4. adım geri konan ağacı o değere karşı sınar.

```bash
# 1) Geri alınacak sürümün KİMLİĞİNİ önce oku — henüz hiçbir şey taşınmadı.
#    TEK SATIR ve boşluksuz okunur: çok satırlı ya da boşluklu bir değer
#    aşağıdaki karşılaştırmayı yozlaştırabilir.
ONCEKI_SHA=$(sudo head -n 1 /opt/harman-zamani-onceki-<TS>/deploy/RELEASE_SHA | tr -d '[:space:]') \
  || { echo "önceki sürümün RELEASE_SHA'sı okunamadı — hiçbir şey taşınmadı, DUR"; exit 1; }

# 2) Kimlik KULLANILABİLİR mi. Boş bir değerle grep -qx boş satıra
#    eşleşir ve 5. adım DOĞRULAYACAK BİR ŞEYİ OLMADAN geçerdi — ölçüldü.
#    Doğrulanamayan bir kimlikle geri alma yapılmaz.
printf '%s' "$ONCEKI_SHA" | grep -qxE '[0-9a-f]{40}' \
  || { echo "önceki sürümün kimliği BOŞ ya da geçersiz — hiçbir şey taşınmadı, DUR"; exit 1; }

# 3) Bozuk sürümü YERİNDEN AL — silme. Ayrı bir tutma adına taşınır.
sudo mv /opt/harman-zamani /opt/harman-zamani-bozuk-<TS> \
  || { echo "bozuk sürüm yerinden alınamadı — hiçbir şey değişmedi, DUR"; exit 1; }

# 4) Önceki sürümü yerine koy.
sudo mv /opt/harman-zamani-onceki-<TS> /opt/harman-zamani \
  || { echo "GERİ ALMA DURDU — önceki: /opt/harman-zamani-onceki-<TS>, bozuk: /opt/harman-zamani-bozuk-<TS>. İkisi de duruyor, birini elle taşıyın"; exit 1; }

# 5) Geri konan ağaç, 1. adımda okunan sürümün TA KENDİSİ mi. Dosyanın var
#    olması değil, KİMLİĞİN eşleşmesi aranır.
sudo grep -qx "$ONCEKI_SHA" /opt/harman-zamani/deploy/RELEASE_SHA \
  || { echo "geri konan ağaç beklenen sürüm DEĞİL (beklenen $ONCEKI_SHA) — bozuk sürüm /opt/harman-zamani-bozuk-<TS> duruyor, hiçbir şey silinmedi, DUR"; exit 1; }

# 6) Bozuk sürüm SİLİNMEZ; incelenmek üzere durur. Silmek ayrı ve sonraki
#    bir karardır, geri almanın parçası değildir.
```

Her adımın başarısızlığında ne kaldığı:

| Başarısız adım | `/opt/harman-zamani` | Duran ağaçlar | Elle kurtarma |
|---|---|---|---|
| 1 (kimliği oku) | canlı ağaç yerinde | canlı + `-onceki-<TS>` | gerekmiyor, hiçbir şey taşınmadı |
| 2 (kimliği doğrula) | canlı ağaç yerinde | canlı + `-onceki-<TS>` | gerekmiyor |
| 3 (bozuğu yerinden al) | canlı ağaç yerinde | canlı + `-onceki-<TS>` | gerekmiyor |
| 4 (öncekini yerine koy) | yok — **silinmedi, taşındı** | `-bozuk-<TS>` + `-onceki-<TS>` | tek `mv` ile biri yerine konur |
| 5 (kimliği karşılaştır) | geri konan ağaç yerinde | `-bozuk-<TS>` + canlı | ağaç şüpheli ama duruyor; `-bozuk-` de duruyor |
Beş satırın hiçbirinde silme yoktur; doğrulama adımları **durdurur, silmez**. Bu,
`deploy/ci-geri-alma-kapisi.py` ile ölçülür: bu bölümdeki hiçbir komut canlı
dizini silemez.

Deploy başladıktan sonraki geri dönüş bu değildir — imaj geri dönüşü için
"Belirli sürüme geçiş / geri dönüş" bölümüne bakın.

### Neden bu dört doğrulama

- **Ağacın eksiksizliği (4. adım).** Yarım inen bir tarball `RELEASE_SHA`
  kontrolünü geçer, sonra deploy'u `docker-compose.prod.yml` yok diye kırar.
  SHA dosyası ağacın tam olduğunu kanıtlamaz.
- **`.env.production`'ın ayrı yedeği (2. adım).** Git'te olmayan tek dosyadır;
  kopyalama yarıda kalırsa depodan geri getirilemez. Yedeği release dizininin
  içine koymak, dizin takas edilince yedeği de taşır — bu yüzden dışarı.
- **Bayt-özdeşlik (5. ve 6. adım).** `cp` sessizce kısa yazabilir; sağlama
  yerine `cmp` kullanılır çünkü tek bayt farkı bile `DATABASE_URL`'i bozar.
- **Geri alma komutunun önceden yazılması.** Bozulmuş bir sistemde komut
  uydurmak, hatayı büyütmenin en kısa yoludur.

### `-onceki-` yedeğini betik OLUŞTURMAZ

`deploy/sunucu-deploy.sh` release dizininin yedeğini **almaz** —
`/opt/harman-zamani-onceki-*` dizinleri yukarıdaki 6. adımın elle yapılan
`mv`'sinden kalır. Betiğin aldığı yedek veritabanınındır (2/7 adımı,
`/root/backups/pre-deploy-*.dump`), dizinin değil. Takası yapan kişi
`-onceki-` dizinini kendisi oluşturur ve **silmez**.

## Belirli sürüme geçiş / geri dönüş

Geri dönüşte yalnız güncel, sertleştirilmiş çalışma ağacındaki deploy aracı
kullanılır:

1. Hedef immutable SHA'yı `.ROLLBACK_TAG` içinden belirleyin. Dosya yoksa
   çalıştırılabilir rollback hedefi doğrulanamamıştır ve deploy durmuş olmalıdır;
   devam etmeyin. `.ROLLBACK_IMAGE` ile `.ROLLBACK_DIGEST` yalnız tanı içindir,
   `sunucu-deploy.sh` girdisi değildir.
2. Güncel `develop` ağacını `/opt/harman-zamani` altında etkinleştirin; hedef
   eski sürümün kaynak ağacına geçmeyin.
3. Güncel ağaçtan `./deploy/sunucu-deploy.sh <40-haneli-SHA>` komutunu
   çalıştırın.
4. Eski compose'un oluşturabildiği yetim yerel DB'yi kaldırın ve yalnız
   `app`/`proxy` servislerinin kaldığını doğrulayın:

   ```bash
   docker rm -f harman-zamani-db-1 2>/dev/null || true
   docker volume rm harman-zamani_postgres_data 2>/dev/null || true
   docker compose -p harman-zamani -f docker-compose.yml -f docker-compose.prod.yml ps
   ```

> **Yasak:** Korunan eski sürüm ağacındaki `deploy/sunucu-deploy.sh` kesinlikle çalıştırılmamalıdır.
> Eski compose `depends_on: db` nedeniyle yetim, boş bir yerel DB başlatabilir;
> eski betik de `compose exec db pg_dump` ile bu boş yerel veritabanını geçerli yedek sanabilir.
> Bu yüzden rollback hedefi eski olsa bile deploy aracı güncel ağaçtan çalıştırılır.
