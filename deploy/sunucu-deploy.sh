#!/usr/bin/env bash
# Üretim deploy'u: CI'da derlenmiş imajı indirir ve servisi günceller.
#
# İmaj artık BU KUTUDA DERLENMEZ. Derleme 2026-08-05'e kadar burada yapılıyordu;
# Vite derlemesi 1 GB'lik makinede belleği yiyor, dockerd her derlemede şişiyordu
# ve birikim makineyi takasa düşürüp siteyi 15 sn'lik zaman aşımlarına soktu.
# Derleme CI'ya taşındı (.github/workflows/ci.yml → `container` işi); imaj duman
# testinden geçtikten sonra GHCR'ye yayınlanıyor.
#
# Hedef kurulum: harman-zamani (/opt/harman-zamani, compose projesi
# `harman-zamani`, veritabanı HARİCİ RDS). Başka bir kurulum için
# SUNGUR_KOK / SUNGUR_PROJE ile override edin.
#
# Kullanım:
#   ./deploy/sunucu-deploy.sh <40-haneli-commit-sha>   # deploy / geri dön
#
#   BEKLENEN_DIGEST=sha256:<...> ./deploy/sunucu-deploy.sh <sha>
#       İndirilen imajın digest'ini de doğrular. Etiket→digest bağı registry
#       tarafında yeniden yazılabildiği için, doğrulanmış TAM O imajın
#       çalıştığından emin olmak istendiğinde kullanılır.
#
# Etiket olarak 'develop' KABUL EDİLMEZ: hareketli etiket, hangi sürümün canlıya
# çıktığını deploy anında bilinemez kılar.
#
#   KURTARMA_DIZINI=<yol> ./deploy/sunucu-deploy.sh
#       Yalnız writable bir eski konteynerden veri kurtarılmışsa. Dizin
#       `docker cp` çıktısıdır, yani içinde `data/` alt dizini bulunmalıdır.
#       Betik kopyayı kaynak konteynerin manifestiyle KARŞILAŞTIRIR; eşleşmezse
#       deploy durur. Beyan değil, doğrulama.
#
# GHCR kimliği: paket özel olduğu için kutunun bir kez `docker login ghcr.io`
# yapmış olması gerekir (yalnız read:packages yetkili bir token ile).
#
# `-e` BURADA KALIR. Bir üretim deploy betiğinde sessizce devam eden bir hata,
# yarım uygulanmış bir sürüm demektir. Testin beklenen-hata çağrıları kendi
# alt kabuğunda izole edilir (deploy/surum-ve-kurtarma-testi.sh::beklenen_hata);
# testi rahatlatmak için üretim betiğinin emniyetini düşürmek yanlış yöndü.
set -euo pipefail

KOK="${SUNGUR_KOK:-/opt/harman-zamani}"
ETIKET="${1:-}"
PROJE="${SUNGUR_PROJE:-harman-zamani}"
APP_KABI="${SUNGUR_APP_KABI:-$PROJE-app-1}"
HACIM_ADI="${SUNGUR_HACIM:-${PROJE}_sungur_data}"
IMAJ_ADI="${SUNGUR_IMAJ_ADI:-ghcr.io/finarfins/nazgul}"
VERI_KOKU="/app/backend/data"
HACIM_KOKU="/opt/sungur-data"
APP_UID_GID="10001:10001"
# Yedek alan geçici konteynerin imajı. SABİT SÜRÜM: `latest` bir gün
# uygulamanın RDS sürümünden eski/yeni bir pg_dump getirir ve yedek ya hiç
# alınamaz ya da geri yüklenemez bir biçimde yazılır.
YEDEK_IMAJI="${SUNGUR_YEDEK_IMAJI:-postgres:16-alpine}"
DC=(docker compose -p "$PROJE" -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production)


# ===========================================================================
# Fonksiyonlar. Betik `SUNGUR_DEPLOY_KAYNAK=evet` ile source edilebilir; o
# durumda akış çalıştırılmaz, yalnız fonksiyonlar tanımlanır. Bunun tek amacı
# deploy/surum-ve-kurtarma-testi.sh'in bu mantığı gerçek koşullarda sınaması.
# ===========================================================================

# --- Etiket sözleşmesi: YALNIZ değişmez commit SHA'sı -----------------------
# `develop` hareketli bir etikettir: bir sonraki merge'de başka bir imajı
# gösterir. Üretimde onunla deploy etmek, hangi sürümün canlıya çıktığını
# deploy anında bilememek demektir; geri dönüş hedefi de aynı belirsizliği
# taşır. Bu yüzden varsayılan KALDIRILDI ve etiket 40 haneli SHA olmak ZORUNDA.
#
# Kaçış yolu bilerek YOK. `SURUM_SHA_ATLA` bir kez eklenip sonra kaldırılmıştı;
# aynı hatayı etiket tarafında tekrarlamıyoruz.
# Tam 40 hane, YALNIZ küçük harf hex. TEK TANIM: hem deploy hedefi hem geri
# dönüş etiketi bunu kullanır. İki ayrı kopya yazmak, birinin bir gün
# gevşetilip diğerinin sıkı kalmasına ve "hangisi geçerli" belirsizliğine
# yol açardı.
#
# Reddedilenler: boş, `develop`, kısa SHA, `sha256:...` digest'i, BÜYÜK harfli
# hex (Docker etiketleri büyük/küçük harf duyarlıdır; `A1B2` başka bir etikettir
# ve karşılaştırmalar sessizce ıskalardı), 40 haneden uzun/kısa her şey.
sha_bicimi_mi() {
  local deger="${1:-}"
  [ "${#deger}" -eq 40 ] || return 1
  [ -z "$(printf '%s' "$deger" | tr -d '0-9a-f')" ] || return 1
  return 0
}

etiket_dogrula() {
  local etiket="${1:-}"
  if sha_bicimi_mi "$etiket"; then
    return 0
  fi
  cat >&2 <<UYARI

HATA: Etiket 40 haneli commit SHA'sı olmalı; verilen: '${etiket:-<boş>}'

Üretime yalnız DEĞİŞMEZ bir etiketle çıkılır. 'develop' bir sonraki merge'de
kayar; onunla açılan sürümün hangi commit olduğu sonradan kanıtlanamaz ve geri
dönüş hedefi de belirsizleşir.

  ./deploy/sunucu-deploy.sh <40-haneli-commit-sha>

UYARI
  return 1
}

# --- Harici RDS yedeği ------------------------------------------------------
# Üretim veritabanı compose'un yönettiği bir konteyner DEĞİL, harici bir RDS
# örneğidir; bu yüzden eski `docker compose exec -T db pg_dump` yolu burada
# ÇALIŞMAZ (ortada `db` servisi yok). Yedek, sabit sürümlü geçici bir postgres
# konteynerinden ağ üzerinden alınır.
#
# SIR SIZDIRMA KORUMASI: bağlantı dizesi komut satırına YAZILMAZ. `docker run`
# argümanları hem `ps` çıktısında hem de kabuk geçmişinde görünür. Değer
# yalnızca değişken ADIYLA (`-e PGURL`) forward edilir, değeri host ortamından
# konteynere doğrudan geçer ve hiçbir yerde basılmaz.
#
# ÜRETİM VERİTABANI DEĞİŞTİRİLMEZ: yalnız `pg_dump` (salt okuma) çalışır.
veritabani_yedegi_al() {
  local hedef="$1" url rc boyut

  # `tr -d '\r'` ZORUNLU. Sunucudaki yapılandırma dosyalarının CRLF satır sonlu
  # olduğu görüldü (docker-compose.prod.yml, deploy/Caddyfile). Linux `grep`
  # satır sonundaki CR'ı SOYMAZ; bağlantı dizesinin sonunda kalan tek bir CR
  # pg_dump'ta "invalid URI" hatası üretir. Fail-closed olurdu ama teşhisi
  # anlamsız: operatör gözle doğru görünen bir URL'e bakıp saatlerce arardı.
  url="$(grep -m1 '^DATABASE_URL=' .env.production 2>/dev/null | cut -d= -f2- | tr -d '\r')"
  if [ -z "$url" ]; then
    echo "HATA: .env.production içinde DATABASE_URL yok; yedek alınamaz." >&2
    return 1
  fi
  # SQLAlchemy sürücü eki (`postgresql+psycopg://`) libpq'nun anlamadığı bir
  # şemadır; pg_dump "invalid URI scheme" ile düşerdi.
  url="$(printf '%s' "$url" | sed -E 's|^postgresql\+[a-z0-9_]+://|postgresql://|')"

  rc=0
  PGURL="$url" docker run --rm -e PGURL "$YEDEK_IMAJI" \
    sh -c 'pg_dump -d "$PGURL" -Fc' > "$hedef" 2>"$hedef.hata" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "HATA: pg_dump başarısız (exit $rc). Deploy durduruldu." >&2
    # stderr'i basıyoruz ama bağlantı dizesini maskeleyerek: libpq hata
    # mesajları host/kullanıcı adı içerebiliyor.
    sed -E 's|postgres(ql)?://[^[:space:]]*|postgresql://<maskeli>|g' "$hedef.hata" >&2
    rm -f "$hedef.hata"
    return 1
  fi
  rm -f "$hedef.hata"

  boyut="$(stat -c %s "$hedef" 2>/dev/null || echo 0)"
  # Boş DEĞİL yetmez: pg_custom biçimi 1 KB'ın altına inemez. `-s` kapısından
  # geçen birkaç baytlık kırpılmış bir dosya "yedek var" görüntüsü verirdi.
  if [ "$boyut" -lt 1024 ]; then
    echo "HATA: yedek şüpheli biçimde küçük ($boyut bayt). Deploy durduruldu." >&2
    return 1
  fi

  # OKUNABİLİRLİK KAPISI. Dosyanın var ve büyük olması geri yüklenebilir olduğu
  # anlamına gelmez; `pg_restore --list` arşiv içindekiler tablosunu ayrıştırır
  # ve bozuk/kırpılmış bir dump'ta hata verir. Veritabanına dokunmaz.
  if ! docker run --rm -i "$YEDEK_IMAJI" pg_restore --list < "$hedef" > /dev/null 2>&1; then
    echo "HATA: yedek pg_restore --list ile okunamıyor; arşiv bozuk. Deploy durduruldu." >&2
    return 1
  fi

  echo "   $hedef"
  echo "   boyut: $boyut bayt ($(du -h "$hedef" | cut -f1))  oluşturma: $(date -u -r "$hedef" '+%Y-%m-%dT%H:%M:%SZ')"
  echo "   pg_restore --list ile okunabilir ✓"
}

# --- Zorunlu geri dönüş işareti ---------------------------------------------
# Eskiden bu adım `|| true` ile sessizce atlanabiliyordu: işaret yazılamazsa
# deploy yine de devam ediyor, sorun çıktığında geri dönülecek sürüm
# bilinmiyordu. Artık çalışan bir konteyner VARSA işaretin yazılması ŞART.
rollback_etiketini_atomik_yaz() {
  local deger="$1" hedef="$HOME/.ROLLBACK_TAG" gecici

  rm -f "$hedef" || return 1
  gecici="$(mktemp "${hedef}.tmp.XXXXXX")" || return 1
  if ! printf '%s\n' "$deger" > "$gecici"; then
    rm -f "$gecici" "$hedef" >/dev/null 2>&1 || true
    return 1
  fi
  if ! mv -f "$gecici" "$hedef"; then
    rm -f "$gecici" "$hedef" >/dev/null 2>&1 || true
    return 1
  fi
  return 0
}

geri_donus_isaretini_yaz() {
  local kap="$1" imaj_id digest revision env_etiket env_sha env_imaj_id hata_nedeni
  local etiket_dogrulandi=0
  if ! docker inspect "$kap" >/dev/null 2>&1; then
    echo "   çalışan konteyner yok (ilk kurulum) — geri dönüş işareti yazılmadı"
    if ! rollback_etiketini_atomik_yaz 'ILK_KURULUM'; then
      echo "HATA: ilk kurulum rollback işareti yazılamadı: $HOME/.ROLLBACK_TAG" >&2
      return 1
    fi
    return 0
  fi

  if ! rm -f "$HOME/.ROLLBACK_TAG"; then
    echo "HATA: bayat rollback etiketi silinemedi: $HOME/.ROLLBACK_TAG" >&2
    return 1
  fi

  imaj_id="$(docker inspect "$kap" --format '{{.Image}}' 2>/dev/null)"
  [ -n "$imaj_id" ] || { echo "HATA: çalışan konteynerin imaj kimliği okunamadı." >&2; return 1; }

  digest="$(docker image inspect "$imaj_id" --format '{{range .RepoDigests}}{{.}}{{"\n"}}{{end}}' 2>/dev/null | head -1)"
  revision="$(docker image inspect "$imaj_id" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null | tr -d '\r' || true)"

  if ! printf '%s\n' "$imaj_id" > "$HOME/.ROLLBACK_IMAGE"; then
    echo "HATA: rollback image kimliği yazılamadı: $HOME/.ROLLBACK_IMAGE" >&2
    return 1
  fi
  if ! printf '%s\n' "${digest:-yok}" > "$HOME/.ROLLBACK_DIGEST"; then
    echo "HATA: rollback digest'i yazılamadı: $HOME/.ROLLBACK_DIGEST" >&2
    return 1
  fi

  if sha_bicimi_mi "$revision"; then
    if ! rollback_etiketini_atomik_yaz "APP_IMAGE_TAG=$revision"; then
      echo "HATA: doğrulanmış OCI rollback etiketi yazılamadı: $HOME/.ROLLBACK_TAG" >&2
      return 1
    fi
    etiket_dogrulandi=1
    echo "   APP_IMAGE_TAG=$revision"
  elif [ -z "$revision" ] || [ "$revision" = "<no value>" ]; then
    env_etiket="$(grep -m1 -E '^APP_IMAGE_TAG=' .env.production 2>/dev/null | tr -d '\r' || true)"
    env_sha="${env_etiket#APP_IMAGE_TAG=}"
    if sha_bicimi_mi "$env_sha"; then
      env_imaj_id="$(docker image inspect "$IMAJ_ADI:$env_sha" --format '{{.Id}}' 2>/dev/null || true)"
      if [ "$env_imaj_id" = "$imaj_id" ]; then
        if ! rollback_etiketini_atomik_yaz "APP_IMAGE_TAG=$env_sha"; then
          echo "HATA: doğrulanmış env rollback etiketi yazılamadı: $HOME/.ROLLBACK_TAG" >&2
          return 1
        fi
        etiket_dogrulandi=1
        echo "   APP_IMAGE_TAG=$env_sha (çalışan image ID ile doğrulanarak .env.production'dan türetildi)"
      else
        hata_nedeni=".env.production APP_IMAGE_TAG yerel imajı çalışan container imajıyla eşleşmiyor"
      fi
    else
      hata_nedeni=".env.production içinde geçerli 40 haneli APP_IMAGE_TAG yok"
    fi
  else
    hata_nedeni="çalışan imajın org.opencontainers.image.revision etiketi geçersiz"
  fi

  if [ "$etiket_dogrulandi" -ne 1 ]; then
    cat >&2 <<HATA
HATA: Çalışan sürüm için çalıştırılabilir rollback etiketi doğrulanamadı.
   ${hata_nedeni:-revision veya doğrulanmış env etiketi bulunamadı}.
   Tanı için .ROLLBACK_IMAGE ve .ROLLBACK_DIGEST yazıldı; deploy durduruldu.
HATA
    return 1
  fi

  echo "   imaj  : $imaj_id"
  echo "   digest: ${digest:-<RepoDigest yok — imaj yerelde derlenmiş olabilir>}"
  return 0
}

# --- Beklenen digest kapısı (isteğe bağlı ama verilirse fail-closed) --------
# BEKLENEN_DIGEST verildiyse, indirilen imajın digest'i birebir eşleşmelidir.
# Etiket→digest bağı GHCR tarafında yeniden yazılabilir; bu kapı, doğrulanmış
# tam olarak O imajın çalıştığını kanıtlar.
digest_dogrula() {
  local imaj_ref="$1" beklenen="${2:-}" gercek
  [ -n "$beklenen" ] || { echo "   BEKLENEN_DIGEST verilmedi — digest kapısı atlandı"; return 0; }
  gercek="$(docker image inspect "$imaj_ref" --format '{{range .RepoDigests}}{{.}}{{"\n"}}{{end}}' 2>/dev/null \
            | sed -n 's|.*@\(sha256:[0-9a-f]*\).*|\1|p' | head -1)"
  if [ -z "$gercek" ]; then
    echo "HATA: '$imaj_ref' için RepoDigest okunamadı; digest doğrulanamaz." >&2; return 1
  fi
  if [ "$gercek" != "$beklenen" ]; then
    echo "HATA: imaj digest'i beklenenle eşleşmiyor. Deploy durduruldu." >&2
    echo "  beklenen: $beklenen" >&2
    echo "  gelen   : $gercek" >&2
    return 1
  fi
  echo "   digest doğrulandı: $gercek ✓"
}

# --- Koşuya özel geçici alan ------------------------------------------------
# Manifest ve fark dosyaları eskiden SABİT /tmp yollarındaydı
# (/tmp/kurtarma-manifest.txt, /tmp/kurtarma-fark.txt). İki somut riski vardı:
#   1. Paralel deploy: iki koşu aynı dosyaya yazar, hacmi_tohumla yanlış
#      manifestle çakışma denetimi ve kopyalama sonrası doğrulama yapardı.
#   2. Bayat dosya: yarıda kalmış eski bir koşudan kalan manifest, kurtarma
#      doğrulaması hiç çalışmamışken bile "var" görünürdü.
# Artık her koşu kendi mktemp dizinini alır; temizlik yalnız O koşunun dizinini
# siler. Dizin İLK KULLANIMDA yaratılır (lazy): betik test harness'i tarafından
# source edildiğinde gereksiz dizin açılmasın diye.
SUNGUR_GECICI_DIZIN=""
KURTARMA_MANIFEST=""
KURTARMA_FARK=""

_gecici_alan_hazirla() {
  [ -n "$SUNGUR_GECICI_DIZIN" ] && return 0
  SUNGUR_GECICI_DIZIN="$(mktemp -d "${TMPDIR:-/tmp}/sungur-deploy-XXXXXXXXXX")"
  KURTARMA_MANIFEST="$SUNGUR_GECICI_DIZIN/kurtarma-manifest.txt"
  KURTARMA_FARK="$SUNGUR_GECICI_DIZIN/kurtarma-fark.txt"
}

# Yalnız bu koşunun dizinini siler. Ad kalıbı kontrolü, değişken beklenmedik
# bir şeye ayarlanırsa `rm -rf` kazasını önler.
gecici_alan_temizle() {
  case "${SUNGUR_GECICI_DIZIN:-}" in
    */sungur-deploy-??????????*) rm -rf "$SUNGUR_GECICI_DIZIN" ;;
  esac
  SUNGUR_GECICI_DIZIN=""; KURTARMA_MANIFEST=""; KURTARMA_FARK=""
}

# Tohumlama yarıda kalırsa volume'da kalan işaret dosyası. Manifestlerin
# DIŞINDA tutulur (aşağıdaki find filtresi), yoksa hash karşılaştırmalarını
# bozardı.
TOHUMLAMA_ISARETI=".sungur-tohumlama-devam-ediyor"

# Manifest satır biçimi: "<sha256> <bayt> <göreli-yol>", yola göre sıralı.
# Hem konteyner hem host tarafında BİREBİR aynı biçim üretilir ki `diff`
# doğrudan anlamlı olsun. Tohumlama işareti bilinçli olarak dışarıda bırakılır.
_manifest_kabugu='cd "$0" 2>/dev/null || exit 0
find . -type f ! -name ".sungur-tohumlama-devam-ediyor" | LC_ALL=C sort | while IFS= read -r f; do
  printf "%s %s %s\n" "$(sha256sum "$f" | cut -d" " -f1)" "$(stat -c %s "$f")" "$f"
done'

# Çalışan bir konteynerin veri kökünden manifest üretir.
konteyner_manifesti() {
  docker exec "$1" sh -c "$_manifest_kabugu" "$2"
}

# Host üzerindeki bir dizinden manifest üretir.
dizin_manifesti() {
  [ -d "$1" ] || return 0
  sh -c "$_manifest_kabugu" "$1"
}

# Named volume'dan manifest üretir (kök yetkisiyle salt okuma).
hacim_manifesti() {
  docker run --rm -u 0:0 -v "$1:/vk:ro" "$2" sh -c "$_manifest_kabugu" /vk
}

# --- 0) Mount envanteri — bilinmeyen veri kaynağı fail-closed ---------------
# Kurtarma yalnız $VERI_KOKU'na (konteynerin writable katmanı) ve kalıcı
# $HACIM_KOKU volume'una bakar. Konteynerde BAŞKA bir bind mount, anonymous
# volume ya da farklı adlı named volume varsa, içindeki attachment/yedekler
# geçişin tamamen dışında kalır ve `up -d` sonrası kaybolur. Bu yüzden mount
# listesi bir ALLOWLIST'e karşı doğrulanır ve tanınmayan tek bir kaynak bile
# deploy'u durdurur.
#
# Otomatik taşıma BİLİNÇLİ OLARAK YOK: bilinmeyen bir kaynağın hangi veriye
# karşılık geldiğini betik bilemez; yanlış yere kopyalamak sessiz bozulma
# üretirdi. Betik yalnız bulduğunu raporlar, kararı operatöre bırakır.
#
# tmpfs bağlamaları burada görünmez (HostConfig.Tmpfs altındadır) ve zaten
# kalıcı veri taşımaz — kapsam dışıdır.
#
# BEKLENEN (tek kabul edilen) mount:
#   volume | $HACIM_ADI | -> /opt/sungur-data
# Hiç mount olmaması da geçerlidir: kalıcı hacim öncesi eski kurulumda veri
# writable katmandadır, kurtarma dalı tam olarak bunun içindir.
mount_denetimi() {
  local kap="$1" mount_satirlari bilinmeyen=0 sayi=0
  mount_satirlari="$(docker inspect "$kap" \
    --format '{{range .Mounts}}{{.Type}}|{{if .Name}}{{.Name}}{{else}}-{{end}}|{{.Destination}}|{{.Source}}{{"\n"}}{{end}}' \
    2>/dev/null)" || {
      echo "HATA: '$kap' mount listesi okunamadı; denetim yapılamadan devam edilmez." >&2
      return 1
    }

  echo "   mount envanteri ($kap):"
  local tip ad hedef kaynak
  while IFS='|' read -r tip ad hedef kaynak; do
    [ -n "${tip:-}" ] || continue
    sayi=$((sayi + 1))
    if [ "$tip" = "volume" ] && [ "$ad" = "$HACIM_ADI" ] && [ "$hedef" = "$HACIM_KOKU" ]; then
      echo "     [beklenen]   volume $ad -> $hedef"
    else
      # Kaynak yolu tanı için gereklidir; parola/secret içermez (host yolu).
      echo "     [BİLİNMEYEN] $tip ${ad} -> ${hedef} (kaynak: ${kaynak:--})" >&2
      bilinmeyen=$((bilinmeyen + 1))
    fi
  done <<EOF
$mount_satirlari
EOF
  [ "$sayi" -eq 0 ] && echo "     (mount yok — kalıcı hacim öncesi kurulum, beklenen bir durum)"

  if [ "$bilinmeyen" -gt 0 ]; then
    cat >&2 <<UYARI

HATA: Konteynerde $bilinmeyen adet TANINMAYAN mount var. Deploy durduruldu.

Beklenen tek mount: volume '$HACIM_ADI' -> $HACIM_KOKU
Yukarıda [BİLİNMEYEN] işaretli her kaynak, bu geçişin GÖRMEDİĞİ bir veri
taşıyor olabilir; \`up -d\` konteyneri yeniden yaratınca bağlantısı kopar.

Betik bunları OTOMATİK TAŞIMAZ — hangi verinin nereye ait olduğu bilinemez.
Yapılacak: her kaynağı elle inceleyin, içeriğini kalıcı hacme taşıyıp
taşımayacağınıza karar verin, sonra deploy'u yeniden çalıştırın.

  docker inspect $kap --format '{{json .Mounts}}' | python3 -m json.tool

Ayrıntı: docs/ops/DEPLOY.md → "Bilinmeyen mount çıkarsa"

UYARI
    return 1
  fi
  echo "   mount sözleşmesi ✓"
}

# Uygulama dururken/durdurulmadan kaynak writable katmandan snapshot çıkarır.
kaynak_snapshot_al() {
  local kap="$1" hedef="$2"
  rm -rf "$hedef"
  mkdir -p "$hedef"
  docker cp "$kap:$VERI_KOKU" "$hedef"
}

# --- 1) Sürüm sözleşmesi ---------------------------------------------------
# TAMAMEN FAIL-CLOSED. Kaçış yolu YOKTUR: eksik, okunamayan ve farklı SHA'nın
# üçü de exit 1'dir. Gerekçe: sunucuya kod `git pull` ile gelmiyor, deploy
# tarball + dizin takası (docs/ops/DEPLOY.md → "Sürüm aktarımı"). Compose ile imaj
# bağımsız eskiyebiliyor ve iki uyumsuz hâl de sessiz:
#   - compose mount ediyor + imajda /opt/sungur-data yok -> volume root:root
#     doğar, non-root uygulama yazamaz, attachment/yedek 500 verir.
#   - imaj hazır + compose mount etmiyor -> veri read-only köke yazılmaya
#     çalışılır, kalıcı olmaz.
# "Uyarıp devam etmek" bu sözleşmeyi anlamsız kılardı; kontrol edilemeyen bir
# şeyi geçerli saymak, kontrol etmemekle aynıdır.
surum_sozlesmesi_dogrula() {
  local imaj_ref="$1" sahip disk_sha imaj_sha cozum

  sahip="$(docker run --rm --entrypoint stat "$imaj_ref" -c '%u:%g' "$HACIM_KOKU" 2>/dev/null || echo yok)"
  if [ "$sahip" != "$APP_UID_GID" ]; then
    cat >&2 <<UYARI

HATA: İmaj kalıcı veri sözleşmesini karşılamıyor.
  imaj              : $imaj_ref
  $HACIM_KOKU  : ${sahip} (beklenen $APP_UID_GID)

Bu imaj, kalıcı volume desteğini ekleyen Dockerfile'dan ÖNCE derlenmiş. Compose
volume'u bağlarsa Docker onu root:root olarak yaratır ve non-root uygulama
içine yazamaz. Deploy durduruldu.

Çözüm: değişikliği içeren commit'i develop'a merge edip CI'ın yayınladığı imajı
kullanın, ya da o commit'in SHA'sını verin:
  ./deploy/sunucu-deploy.sh <commit-sha>

UYARI
    return 1
  fi
  echo "   imaj: $HACIM_KOKU = $sahip ✓"

  cozum="$("${DC[@]}" config 2>/dev/null)" || {
    echo "HATA: 'docker compose config' çözümlenemedi." >&2; return 1; }
  echo "$cozum" | grep -q "SUNGUR_DATA_DIR: $HACIM_KOKU" \
    || { echo "HATA: compose SUNGUR_DATA_DIR: $HACIM_KOKU geçirmiyor — docker-compose.prod.yml eski." >&2; return 1; }
  echo "$cozum" | grep -q 'source: sungur_data' \
    || { echo "HATA: compose sungur_data mount'u yok — docker-compose.prod.yml eski." >&2; return 1; }
  echo "$cozum" | grep -q 'read_only: true' \
    || { echo "HATA: compose read_only: true yok — sertleştirme kaybolmuş." >&2; return 1; }
  echo "   compose: SUNGUR_DATA_DIR + sungur_data mount + read_only ✓"

  # deploy/RELEASE_SHA `git archive` sırasında export-subst ile gerçek commit
  # SHA'sına dönüşür (.gitattributes). Git deposundan çalışılıyorsa dosya hâlâ
  # `$Format:...$` yer tutucusudur; yalnız o durumda `git rev-parse` devreye
  # girer. İkisi de yoksa SHA bilinmiyor demektir — ve bilinmeyen bir sürümle
  # devam etmek tam olarak engellenmek istenen şeydir.
  disk_sha="$(tr -d '[:space:]' < deploy/RELEASE_SHA 2>/dev/null || true)"
  case "$disk_sha" in
    ''|\$Format:*) disk_sha="$(git rev-parse HEAD 2>/dev/null || true)" ;;
  esac
  if [ -z "$disk_sha" ]; then
    cat >&2 <<'UYARI'

HATA: Diskteki sürümün SHA'sı belirlenemedi.
deploy/RELEASE_SHA okunamadı ya da hâlâ `$Format:%H$` yer tutucusu ve bir git
deposu da yok. Sürüm hizası doğrulanamadığı için deploy durduruldu.

Sürümü `git archive` ile paketleyip aktarın; export-subst RELEASE_SHA'yı
gerçek commit SHA'sına çevirir:
  git archive --format=tar.gz -o release.tar.gz <commit-sha>

UYARI
    return 1
  fi

  imaj_sha="$(docker image inspect "$imaj_ref" \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null | tr -d '[:space:]')"
  if [ -z "$imaj_sha" ] || [ "$imaj_sha" = "<novalue>" ]; then
    cat >&2 <<UYARI

HATA: İmajda org.opencontainers.image.revision etiketi yok.
  imaj: $imaj_ref

Etiketi CI basar (.github/workflows/ci.yml, 'Build production image'). Etiketsiz
bir imaj, hangi commit'ten geldiği bilinmeyen bir imajdır; sürüm hizası
doğrulanamaz. Deploy durduruldu.

UYARI
    return 1
  fi

  if [ "$disk_sha" != "$imaj_sha" ]; then
    cat >&2 <<UYARI

HATA: Diskteki sürüm ile imaj aynı commit'ten değil.
  disk (deploy/RELEASE_SHA) : $disk_sha
  imaj (OCI revision)       : $imaj_sha

docker-compose.prod.yml, deploy/ ve Dockerfile aynı commit kapsamında
taşınmalıdır. Sunucuya kod git ile gelmediği için bu tek otomatik kontroldür.
Kaçış yolu bilerek YOKTUR — uyumsuz yapılandırmayla açılan bir sürüm, bu PR'ın
kapatmaya çalıştığı sessiz veri kaybının ta kendisidir.

Çözüm — sürümü imajla aynı commit'ten yeniden aktarın:
  git archive --format=tar.gz -o release.tar.gz $imaj_sha
  # sunucuda: tarball'u açıp dizini takas edin
  # adımlar: docs/ops/DEPLOY.md → "Sürüm aktarımı — dizin takası"

UYARI
    return 1
  fi
  echo "   sürüm hizası: $imaj_sha ✓"
}

# --- 2) Kurtarma doğrulaması ------------------------------------------------
# Operatör beyanı YETMEZ. Kaynak konteynerin manifesti ile host'taki kopyanın
# manifesti satır satır karşılaştırılır: yol + boyut + SHA-256. Eksik, fazla,
# boş ya da içeriği değişmiş tek bir dosya deploy'u durdurur.
#
# 3. parametre (kaynak_snapshot) İSTEĞE BAĞLIDIR ve `${3:-}` ile okunur:
# `local ... kaynak_snapshot="$3"` yazmak, iki argümanla çağrıldığında set -u
# altında fonksiyonu DOĞRULAMAYA HİÇ VARMADAN öldürüyordu. Bu, "sıfırdan farklı
# döndü" diye bakan bir teste geçmiş gibi görünür — sahte pozitif. Bu yüzden
# her beklenen-hata testi artık ayrıca ÖZEL hata mesajını da doğruluyor.
kurtarma_dogrula() {
  local kap="$1" kurtarma="$2" kaynak_man kopya_man
  local kaynak_snapshot="${3:-}"
  local manifest_yolu="${4:-}"
  local kopya_kok="$kurtarma/data"
  local kaynak_kok

  if [ ! -d "$kopya_kok" ]; then
    echo "HATA: '$kopya_kok' yok. KURTARMA_DIZINI, 'docker cp' çıktısını içeren dizin olmalı." >&2
    return 1
  fi
  kaynak_man="$(mktemp)"; kopya_man="$(mktemp)"
  if [ -n "${kaynak_snapshot:-}" ]; then
    kaynak_kok="$kaynak_snapshot/data"
    [ -d "$kaynak_kok" ] || { echo "HATA: kaynak snapshot '$kaynak_snapshot' geçersiz." >&2; return 1; }
    dizin_manifesti "$kaynak_kok" > "$kaynak_man" || {
      echo "HATA: kaynak snapshot manifesti üretilemedi." >&2; return 1; }
  else
    konteyner_manifesti "$kap" "$VERI_KOKU" > "$kaynak_man" || {
      echo "HATA: kaynak konteynerden manifest üretilemedi." >&2; return 1; }
  fi
  dizin_manifesti "$kopya_kok" > "$kopya_man" || {
    echo "HATA: kurtarma kopyasından manifest üretilemedi." >&2; return 1; }

  local kaynak_adet kopya_adet
  kaynak_adet="$(grep -c . "$kaynak_man" || true)"
  kopya_adet="$(grep -c . "$kopya_man" || true)"
  echo "   kaynak konteyner : $kaynak_adet dosya"
  echo "   kurtarma kopyası : $kopya_adet dosya ($kopya_kok)"

  if [ "$kaynak_adet" -eq 0 ]; then
    echo "HATA: kaynak konteynerde dosya yok; kurtarma dizini vermek anlamsız." >&2
    rm -f "$kaynak_man" "$kopya_man"; return 1
  fi
  _gecici_alan_hazirla
  [ -n "$manifest_yolu" ] || manifest_yolu="$KURTARMA_MANIFEST"
  if [ "$manifest_yolu" != "$KURTARMA_MANIFEST" ]; then
    echo "HATA: kurtarma manifesti bu koşunun geçici alanına ait değil; deploy durduruldu." >&2
    rm -f "$kaynak_man" "$kopya_man"
    return 1
  fi
  if ! diff -u "$kaynak_man" "$kopya_man" > "$KURTARMA_FARK" 2>&1; then
    echo "HATA: kurtarma kopyası kaynakla EŞLEŞMİYOR. Deploy durduruldu." >&2
    echo "  (- kaynakta var/farklı, + kopyada var/farklı; yol boyut sha256)" >&2
    sed -n '3,40p' "$KURTARMA_FARK" | sed 's/^/    /' >&2
    rm -f "$kaynak_man" "$kopya_man"; return 1
  fi
  cp "$kaynak_man" "$manifest_yolu"
  rm -f "$kaynak_man" "$kopya_man"
  echo "   manifest eşleşmesi (yol + boyut + sha256): ✓"
}

# --- 3) Volume'u UYGULAMA BAŞLAMADAN tohumla --------------------------------
# Sıra kritik: uygulama açıldıktan sonra kopyalamak, aradaki pencerede yazılan
# yeni bir attachment'ın `cp -an` tarafından atlanmasına ve kurtarılan dosyanın
# sessizce dışarıda kalmasına yol açar. Bu yüzden app DURUYORKEN çalışır ve
# çakışma bulursa sessizce geçmez — listeler ve durur.
hacmi_tohumla() {
  # `local` önce TÜM adları bildirip sonra atadığı için aynı deyimde
  # kopya_kok="$kurtarma/data" yazmak set -u altında "unbound variable" verir.
  local kurtarma="$1" imaj_ref="$2" manifest_yolu="${3:-}"
  local kopya_kok="$kurtarma/data"

  # Manifest BU KOŞUDA üretilmiş olmalı. Sabit /tmp yolu döneminde, kurtarma
  # doğrulaması hiç çalışmamışken bayat bir dosya "geçerli" sayılabiliyordu.
  [ -n "$manifest_yolu" ] || manifest_yolu="${KURTARMA_MANIFEST:-}"
  if [ -z "${KURTARMA_MANIFEST:-}" ] || [ "$manifest_yolu" != "$KURTARMA_MANIFEST" ]; then
    echo "HATA: kurtarma manifesti bu koşunun geçici alanına ait değil; tohumlama yapılamaz." >&2
    return 1
  fi
  if [ ! -s "$manifest_yolu" ]; then
    echo "HATA: bu koşuya ait kurtarma manifesti yok; kurtarma_dogrula çalışmadan tohumlama yapılamaz." >&2
    return 1
  fi

  docker volume create "$HACIM_ADI" >/dev/null

  # --- Yarım kalmış tohumlama ---------------------------------------------
  # Kopyalama sırasında deploy öldürülürse volume yarı dolu kalır. İşaret
  # dosyası hâlâ oradaysa bunu ANLAŞILIR biçimde söyleriz; "hedefte dosya var"
  # gibi genel bir çakışma hatası operatörü yanlış yöne sürüklerdi.
  # Hiçbir dosya SİLİNMEZ, üzerine YAZILMAZ — karar operatörün.
  # VARLIK sorulur, İÇERİK değil. `cat` çıktısının boş olmamasına bakmak,
  # işaret dosyası yaratıldıktan sonra ama içeriği yazılmadan önce kesilen bir
  # koşuyu GÖRMEZDEN gelirdi: dosya orada durur, `cat` boş döner, kapı açık
  # kalır. Dosyanın kendisi tek başına "tohumlama başladı" demektir.
  #
  # Üç durum ayrılır ve bilinmeyen durum da fail-closed'dur: yoklama herhangi
  # bir nedenle çalışmazsa (daemon hatası, imaj yok) "işaret yok" varsaymak,
  # tam olarak korunmaya çalışılan hâlde deploy'u sürdürürdü.
  local isaret_durum isaret_icerik
  isaret_durum="$(docker run --rm -u 0:0 -v "$HACIM_ADI:/vk:ro" "$imaj_ref" \
    sh -c "if [ -e \"/vk/$TOHUMLAMA_ISARETI\" ]; then printf 'VAR:'; cat \"/vk/$TOHUMLAMA_ISARETI\" 2>/dev/null; else printf 'YOK'; fi" \
    2>/dev/null)" || isaret_durum="OKUNAMADI"
  case "$isaret_durum" in
    YOK) ;;
    VAR*)
      isaret_icerik="${isaret_durum#VAR:}"
      cat >&2 <<UYARI

HATA: Önceki bir tohumlama YARIDA KALMIŞ. Deploy durduruldu.

  volume  : $HACIM_ADI
  işaret  : $HACIM_KOKU/$TOHUMLAMA_ISARETI
  içerik  : ${isaret_icerik:-(boş — işaret yazılırken kesilmiş)}

Volume kısmen dolu olabilir. Betik bu durumda hiçbir dosyayı silmez, üzerine
yazmaz ve kopyalamaya devam etmez — hangi dosyanın tam, hangisinin yarım
yazıldığı buradan bilinemez.

Ayrıntı ve güvenli devam adımları: docs/ops/DEPLOY.md → "Yarım kalmış tohumlama"

UYARI
      return 1
      ;;
    *)
      echo "HATA: tohumlama işareti okunamadı (yanıt: '${isaret_durum}'). Yarım bir tohumlama gizli kalabilir; deploy durduruldu." >&2
      return 1
      ;;
  esac

  # Çakışma denetimi: hedefte aynı yolda dosya varsa `cp -an` onu atlar ve
  # kurtarılan sürüm sessizce kaybolurdu. Önceden bakılır.
  local hedef_man cakisma
  hedef_man="$(mktemp)"
  hacim_manifesti "$HACIM_ADI" "$imaj_ref" > "$hedef_man" 2>/dev/null || true
  cakisma="$(awk 'NR==FNR{h[$3]=1;next} ($3 in h){print $3}' \
             "$hedef_man" "$manifest_yolu" || true)"
  if [ -n "$cakisma" ]; then
    echo "HATA: hedef volume'da aynı yolda dosya(lar) var; cp -an bunları sessizce atlardı." >&2
    echo "$cakisma" | head -20 | sed 's/^/    çakışma: /' >&2
    echo "  Elle inceleyip çözün; betik hiçbir dosyayı silmez veya üzerine yazmaz." >&2
    rm -f "$hedef_man"; return 1
  fi
  rm -f "$hedef_man"

  # İşareti kopyalamadan ÖNCE bırak: kesinti tam da bu iki adım arasında
  # olabilir ve o zaman bir sonraki koşu yukarıdaki dalda durur.
  docker run --rm -u 0:0 -v "$HACIM_ADI:/hedef" "$imaj_ref" \
    sh -c "printf '%s\n' \"baslangic=\$(date -u +%Y-%m-%dT%H:%M:%SZ) pid=$$ kaynak=$kopya_kok\" > /hedef/$TOHUMLAMA_ISARETI" \
    >/dev/null || { echo "HATA: tohumlama işareti yazılamadı." >&2; return 1; }

  # cp -an: mevcut hiçbir dosyanın üzerine yazmaz. chown tek seferliktir ve
  # yalnız bu kurtarma yolunda çalışır — normal açılışta recursive chown yok.
  docker run --rm -u 0:0 \
    -v "$HACIM_ADI:/hedef" -v "$kopya_kok:/kaynak:ro" "$imaj_ref" \
    sh -c 'cp -an /kaynak/. /hedef/ && chown -R 10001:10001 /hedef' \
    || { echo "HATA: kurtarılan dosyalar volume'a kopyalanamadı (işaret bırakıldı)." >&2; return 1; }

  # Kopyalama sonrası hedef manifesti kaynağın ÜST KÜMESİ olmalı ve kurtarılan
  # her yol aynı boyut + sha256 ile bulunmalı.
  local son_man eksik
  son_man="$(mktemp)"
  hacim_manifesti "$HACIM_ADI" "$imaj_ref" > "$son_man"
  eksik="$(awk 'NR==FNR{h[$1" "$2" "$3]=1;next} !($1" "$2" "$3 in h){print $3}' \
           "$son_man" "$manifest_yolu" || true)"
  if [ -n "$eksik" ]; then
    echo "HATA: kopyalama sonrası doğrulama başarısız; şu yollar hedefte aynı hash ile yok:" >&2
    echo "$eksik" | head -20 | sed 's/^/    /' >&2
    rm -f "$son_man"; return 1
  fi
  rm -f "$son_man"

  # Yalnız doğrulama GEÇTİKTEN sonra işaret kalkar. Kendi bıraktığımız işaret
  # dosyasıdır; kullanıcı verisi değildir.
  docker run --rm -u 0:0 -v "$HACIM_ADI:/hedef" "$imaj_ref" \
    sh -c "rm -f /hedef/$TOHUMLAMA_ISARETI" >/dev/null \
    || { echo "HATA: tohumlama işareti kaldırılamadı; sonraki koşu yarım sanacak." >&2; return 1; }
  echo "   volume tohumlandı ve manifest doğrulandı ✓"
}

if [ "${SUNGUR_DEPLOY_KAYNAK:-}" = "evet" ]; then
  return 0 2>/dev/null || exit 0
fi

# ===========================================================================
# Akış
# ===========================================================================
# Geçici alan temizliği YALNIZ burada kurulur. Betik source edildiğinde
# (SUNGUR_DEPLOY_KAYNAK=evet) trap kurulmaz — kuruluysa çağıran harness'in
# kendi EXIT trap'ini ezer ve onun konteyner/volume temizliği hiç çalışmazdı.
trap gecici_alan_temizle EXIT

# Etiket kapısı EN BAŞTA: geçersiz bir etiketle hiçbir şeye dokunulmadan durulur.
etiket_dogrula "$ETIKET" || exit 1

cd "$KOK"

echo "== 1/7 Ön kontrol: writable konteyner / veri kurtarma =="
# Docker'a ulaşılamıyorsa `docker inspect` de başarısız olur ve aşağıdaki
# "konteyner yok" dalı yanlışlıkla "ilk kurulum, güvenli" derdi.
docker info >/dev/null 2>&1 || {
  echo "HATA: Docker daemon'a ulaşılamıyor; ön kontrol yapılamaz. Deploy durduruldu." >&2
  exit 1
}

KURTARMA_VAR=hayir
if ! docker inspect "$APP_KABI" >/dev/null 2>&1; then
  echo "   çalışan app konteyneri yok (ilk kurulum) — geçiş güvenli"
elif [ "$(docker inspect "$APP_KABI" \
          --format '{{.HostConfig.ReadonlyRootfs}}' 2>/dev/null | tr -d '[:space:]')" = "true" ]; then
  echo "   çalışan konteynerin kökü read-only — writable layer'da veri olamaz, geçiş güvenli"
elif [ -n "${KURTARMA_DIZINI:-}" ]; then
  [ -d "$KURTARMA_DIZINI/data" ] || { echo "HATA: '$KURTARMA_DIZINI/data' yok. KURTARMA_DIZINI, 'docker cp' çıktısı olmalı." >&2; exit 1; }
  echo "   konteyner kökü yazılabilir; kurtarma kopyası alındıysa kabul edilir, asıl eşleme geçişte yapılacak."
  KURTARMA_VAR=evet
else
  echo "   konteyner kökü yazılabilir; $VERI_KOKU içeriği:" >&2
  konteyner_manifesti "$APP_KABI" "$VERI_KOKU" 2>/dev/null | head -30 | sed 's/^/     /' >&2 || true
  cat >&2 <<UYARI

HATA: Çalışan app konteynerinin kök dosya sistemi read-only DEĞİL.
Writable layer'da attachment veya yedek birikmiş olabilir; \`up -d\` konteyneri
yeniden yaratıp bunları geri dönüşsüz siler. Deploy durduruldu.

ADIM 1 — veriyi kutuya çıkarın (hiçbir şey silinmez, yalnız kopyalanır):

  KURTARMA="\$HOME/kurtarma-\$(date +%Y%m%d-%H%M%S)"
  mkdir -p "\$KURTARMA"
  docker cp $APP_KABI:$VERI_KOKU "\$KURTARMA"

ADIM 2 — kopyayı betiğe DOĞRULATARAK devam edin. Beyan yeterli değildir:
betik kaynak konteynerin manifestini (yol + boyut + sha256) üretip kopyayla
karşılaştırır, eşleşmezse durur. Kurtarılan dosyalar uygulama BAŞLAMADAN önce
volume'a taşınır.

  KURTARMA_DIZINI="\$KURTARMA" ./deploy/sunucu-deploy.sh $ETIKET

Kurtarma dizinini betik SİLMEZ; doğrulama geçtikten sonra siz saklarsınız.

Ayrıntı: docs/ops/DEPLOY.md → "Kalıcı veri hacmine ilk geçiş"

UYARI
  exit 1
fi

# Mount envanteri. KONUM KRİTİK: `pull` ve `up -d`'den ÖNCE, kurtarma
# başlamadan. Bilinmeyen bir bind/anonymous/named volume varsa taşınacak
# verinin tamamı görülmemiş demektir; o hâlde hiçbir şeye dokunmadan durulur.
# Konteyner yoksa (ilk kurulum) denetlenecek mount da yoktur.
if docker inspect "$APP_KABI" >/dev/null 2>&1; then
  mount_denetimi "$APP_KABI" || exit 1
fi

echo "== 2/7 Veritabanı yedeği (harici RDS) =="
mkdir -p "$HOME/backups"
YEDEK="$HOME/backups/pre-deploy-$(date +%Y%m%d-%H%M%S).dump"
veritabani_yedegi_al "$YEDEK" || exit 1

echo "== 3/7 Geri dönüş işareti =="
geri_donus_isaretini_yaz "$APP_KABI" || exit 1

echo "== 4/7 İmaj indiriliyor: $ETIKET =="
# `docker pull`, `compose pull app` DEĞİL. Compose etiketi .env.production'dan
# çözerdi ve bu da dosyanın doğrulamadan ÖNCE yazılmasını zorunlu kılardı.
# Doğrudan çekiş, indirme ile yapılandırmayı birbirinden ayırır: tam olarak
# istenen referans indirilir ve dosyaya hiç dokunulmaz.
docker pull "$IMAJ_ADI:$ETIKET" || exit 1

echo "== 5/7 İmaj ↔ yapılandırma sözleşmesi =="
digest_dogrula "$IMAJ_ADI:$ETIKET" "${BEKLENEN_DIGEST:-}" || exit 1
surum_sozlesmesi_dogrula "$IMAJ_ADI:$ETIKET" || exit 1

# .env.production YALNIZ BURADA yazılır — indirme, digest ve sürüm sözleşmesi
# üçü de geçtikten SONRA.
#
# Eskiden etiket 4/7'nin başında yazılıyordu. 5/7 başarısız olduğunda dosya
# DOĞRULANMAMIŞ etiketi göstermeye devam ediyordu: deploy durmuş olsa bile
# sonraki herhangi bir `docker compose up -d` (operatör, bakım betiği, restart
# politikası) o imajı canlıya alırdı. Kapı, arkasında açık kalan bir kapı
# bıraktığı sürece kapı değildir.
etiketi_yaz() {
  local etiket="$1" gecici
  gecici="$(mktemp)"
  if grep -qE '^APP_IMAGE_TAG=' .env.production; then
    sed "s|^APP_IMAGE_TAG=.*|APP_IMAGE_TAG=$etiket|" .env.production > "$gecici"
  else
    cat .env.production > "$gecici"
    printf 'APP_IMAGE_TAG=%s\n' "$etiket" >> "$gecici"
  fi
  # İzinleri koru: .env.production sırlar içerir, mktemp 0600 verir ama
  # dosyanın kendi modu farklı olabilir; içeriği yerinde değiştiriyoruz.
  cat "$gecici" > .env.production
  rm -f "$gecici"
}
etiketi_yaz "$ETIKET"
echo "   .env.production güncellendi: APP_IMAGE_TAG=$ETIKET"

echo "== 6/7 Kalıcı hacim hazırlığı =="
if [ "$KURTARMA_VAR" = "evet" ]; then
  # Uygulama DURUYORKEN tohumla: açık bir uygulamanın yazdığı yeni dosya,
  # kurtarılan eski dosyanın `cp -an` ile atlanmasına yol açardı.
  echo "   eski app durduruluyor (tohumlama uygulama kapalıyken yapılır)"
  "${DC[@]}" stop app >/dev/null 2>&1 || true
  KAYNAK_SNAPSHOT="$(mktemp -d)"
  if ! kaynak_snapshot_al "$APP_KABI" "$KAYNAK_SNAPSHOT"; then
    echo "HATA: uygulama kapanınca kaynak snapshot alinamadi. Deploy durduruldu." >&2
    "${DC[@]}" up -d app >/dev/null 2>&1 || true
    rm -rf "$KAYNAK_SNAPSHOT"
    exit 1
  fi
  _gecici_alan_hazirla
  if ! kurtarma_dogrula "$APP_KABI" "$KURTARMA_DIZINI" "$KAYNAK_SNAPSHOT" "$KURTARMA_MANIFEST"; then
    echo "HATA: kurtarma dizini, kapatılmış uygulamanın son snapshot'ıyla eşleşmiyor. Deploy durduruldu." >&2
    rm -rf "$KAYNAK_SNAPSHOT"
    if "${DC[@]}" start app >/dev/null 2>&1; then
      echo "   eski uygulama güvenli biçimde yeniden başlatıldı; operatör guncel snapshot ile tekrar deneyebilir."
    else
      echo "   eski uygulama yeniden başlatılamadı; güvenli devam için manuel deploy prosedürü uygulanmalı." >&2
    fi
    exit 1
  fi
  hacmi_tohumla "$KURTARMA_DIZINI" "$IMAJ_ADI:$ETIKET" "$KURTARMA_MANIFEST" || exit 1
  rm -rf "$KAYNAK_SNAPSHOT"
else
  echo "   kurtarılacak veri yok — tohumlama gerekmiyor"
fi

echo "== 7/7 Servis güncelleniyor =="
"${DC[@]}" up -d app

echo "   Doğrulama"
for i in $(seq 1 30); do
  if "${DC[@]}" exec -T app sh -c \
      'python -c "import urllib.request;urllib.request.urlopen(\"http://127.0.0.1:5050/api/ready\",timeout=5)"' \
      < /dev/null >/dev/null 2>&1; then
    echo "   /api/ready 200 ✓"
    "${DC[@]}" exec -T app sh -c 'cd /app/backend && alembic current' < /dev/null 2>&1 | tail -1
    if [ "$KURTARMA_VAR" = "evet" ]; then
      echo "   kurtarılan dosyalar volume'da:"
      "${DC[@]}" exec -T app sh -c "find $HACIM_KOKU -type f | wc -l" < /dev/null 2>&1 | sed 's/^/     toplam dosya: /'
      echo "     kurtarma dizini SİLİNMEDİ: $KURTARMA_DIZINI"
    fi
    echo "DEPLOY TAMAM ($ETIKET)"
    exit 0
  fi
  sleep 3
done

echo "HATA: uygulama 90 sn içinde yanıt vermedi. Geri dönüş:" >&2
echo "  ./deploy/sunucu-deploy.sh <onceki-sha>   ya da   $(cat "$HOME/.ROLLBACK_TAG")" >&2
"${DC[@]}" logs app --tail 30 --no-color
exit 1
