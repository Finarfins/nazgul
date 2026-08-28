#!/usr/bin/env bash
# Üretim deploy sözleşmesinin fail-closed testleri.
#
# NEDEN AYRI BİR DOSYA
#   deploy/surum-ve-kurtarma-testi.sh imajın İÇİNDEKİ sözleşmeyi (sürüm hizası,
#   kurtarma manifesti, volume tohumlama) sınar. Burada sınanan şey bir katman
#   dışarısı: DOĞRU imajın DOĞRU depodan çekildiği, üretimde derleme yapılmadığı,
#   koşuların birbirinin geçici dosyasına yazmadığı ve konteynerde tanınmayan bir
#   veri kaynağı varsa deploy'un hiç başlamadığı. Bu boşluk gerçek bir üretim
#   blokajı üretmişti: CI `ghcr.io/finarfins/nazgul`'a yayınlarken compose
#   `ghcr.io/finarfins/sungur-tarim-erp`'den çekiyordu ve hiçbir kapı görmüyordu.
#
# DOCKER DAEMON GEREKMEZ. `docker compose config` istemci tarafıdır; mount ve
# tohumlama senaryoları `docker` komutu stub'lanarak koşulur. Bu sayede test
# hem CI'da hem geliştirici makinesinde aynı biçimde çalışır.
#
# CI: .github/workflows/ci.yml → `container` işi.
# Yerelde: ./deploy/deploy-sozlesme-testi.sh
set -uo pipefail

KOK_DIZIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CALISMA="$(mktemp -d)"
GECTI=0; KALDI=0

yesil()   { printf '  \033[32m✓ %s\033[0m\n' "$*"; GECTI=$((GECTI+1)); }
kirmizi() { printf '  \033[31m✗ %s\033[0m\n' "$*"; KALDI=$((KALDI+1)); }
baslik()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

trap 'rm -rf "$CALISMA"' EXIT
cd "$KOK_DIZIN"

# Kanonik depo. CI bu adı `${{ github.repository }}`'den türetir
# (.github/workflows/ci.yml → "Publish image to GHCR"); compose ve deploy
# betiği aynı adı kullanmak ZORUNDADIR.
KANONIK_IMAJ="ghcr.io/finarfins/nazgul"

# --- Çözümlenmiş compose sözleşmesi -----------------------------------------
# `docker compose config` -f dosyalarını birleştirip nihai hâli verir. Kontrol
# tam olarak burada yapılmalı: tek tek dosyalara bakmak, birleşmeden doğan
# hataları (ör. base'deki `build:` bloğunun prod'da ayakta kalması) kaçırır.
env_uret() {
  local hedef="$1"
  cp .env.production.example "$hedef"
  cat >> "$hedef" <<'EOF'
POSTGRES_PASSWORD=sozlesme-testi-parolasi-yeterince-uzun
TURNSTILE_SECRET_KEY=sozlesme-testi-dummy
BOOTSTRAP_ADMIN_PASSWORD=sozlesme-testi-dummy
EOF
}

# `--format json` bilinçli: PyYAML her runner'da kurulu olmayabilir, `json`
# standart kütüphanededir. Kapının bir bağımlılık yüzünden atlanması, kapının
# hiç olmamasıyla aynı şeydir.
compose_cozumle() {
  local prod_dosya="$1" env_dosya="$2" cikti="$3"
  docker compose -f docker-compose.yml -f "$prod_dosya" --env-file "$env_dosya" \
    config --format json > "$cikti" 2>"$cikti.err"
}

# app servisinin çözümlenmiş `image` ve `build` alanlarını yazdırır.
app_alani() {
  python3 - "$1" "$2" <<'PY'
import sys, json
d = json.load(open(sys.argv[1]))
app = (d.get("services") or {}).get("app") or {}
v = app.get(sys.argv[2], "<YOK>")
print(v if isinstance(v, str) else ("<VAR>" if v != "<YOK>" else "<YOK>"))
PY
}

# Üretim sözleşmesi kapısı: CI bunu doğrudan çağırır.
compose_sozlesmesi_dogrula() {
  local cozum="$1" imaj build
  imaj="$(app_alani "$cozum" image)"
  build="$(app_alani "$cozum" build)"
  case "$imaj" in
    "$KANONIK_IMAJ":*) ;;
    *) echo "HATA: app imajı kanonik depoda değil: $imaj (beklenen ${KANONIK_IMAJ}:<etiket>)" >&2; return 1 ;;
  esac
  if [ "$build" != "<YOK>" ]; then
    echo "HATA: üretim compose'unda app.build hâlâ var; pull başarısız olursa Compose yerelde DERLEMEYE düşer." >&2
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
baslik "A) Çözümlenmiş üretim compose'u"
# ---------------------------------------------------------------------------
ENV_DOSYA="$CALISMA/env"; env_uret "$ENV_DOSYA"
COZUM="$CALISMA/cozum.yml"
if compose_cozumle docker-compose.prod.yml "$ENV_DOSYA" "$COZUM"; then
  yesil "A0 compose config çözümlendi"
else
  kirmizi "A0 compose config çözümlenemedi"; sed -n '1,5p' "$COZUM.err"
fi

A1_IMAJ="$(app_alani "$COZUM" image)"
case "$A1_IMAJ" in
  "$KANONIK_IMAJ":*) yesil "A1 resolved app.image kanonik depoda ($A1_IMAJ)" ;;
  *) kirmizi "A1 resolved app.image yanlış: $A1_IMAJ" ;;
esac

A2_BUILD="$(app_alani "$COZUM" build)"
if [ "$A2_BUILD" = "<YOK>" ]; then
  yesil "A2 resolved app.build yok (üretimde derleme geri düşüşü kapalı)"
else
  kirmizi "A2 resolved app.build hâlâ var — pull hatasında yerel derleme riski"
fi

if compose_sozlesmesi_dogrula "$COZUM" >/dev/null 2>&1; then
  yesil "A3 sözleşme kapısı doğru compose'da GEÇİYOR"
else
  kirmizi "A3 sözleşme kapısı doğru compose'da geçmedi"
fi

# ---------------------------------------------------------------------------
baslik "B) Negatif mutasyonlar — kapı ısırmalı"
# ---------------------------------------------------------------------------
# B1: yanlış depo adı.
YANLIS="$CALISMA/prod-yanlis-depo.yml"
sed 's|ghcr.io/finarfins/nazgul|ghcr.io/finarfins/sungur-tarim-erp|' \
  docker-compose.prod.yml > "$YANLIS"
COZUM_B1="$CALISMA/cozum-b1.yml"
if compose_cozumle "$YANLIS" "$ENV_DOSYA" "$COZUM_B1" \
   && compose_sozlesmesi_dogrula "$COZUM_B1" >/dev/null 2>&1; then
  kirmizi "B1 yanlış depo adı kapıdan GEÇTİ (sahte yeşil)"
else
  yesil "B1 yanlış image repository → kırmızı"
fi

# B2: build bloğu geri geliyor (base'den sızma senaryosu).
BUILDLI="$CALISMA/prod-buildli.yml"
sed 's|^    build: !reset null$|    build:\n      context: .|' \
  docker-compose.prod.yml > "$BUILDLI"
COZUM_B2="$CALISMA/cozum-b2.yml"
if compose_cozumle "$BUILDLI" "$ENV_DOSYA" "$COZUM_B2" \
   && compose_sozlesmesi_dogrula "$COZUM_B2" >/dev/null 2>&1; then
  kirmizi "B2 app.build geri gelince kapı GEÇTİ (sahte yeşil)"
else
  yesil "B2 app.build geri gelirse → kırmızı"
fi

# B3: doğru depo + SHA etiketi geçmeli (etiket biçimi kapıyı kırmamalı).
SHA_ETIKET="32e20ed7704055097cb2e2ff8daed3e702122c8d"
ENV_SHA="$CALISMA/env-sha"; env_uret "$ENV_SHA"
echo "APP_IMAGE_TAG=$SHA_ETIKET" >> "$ENV_SHA"
COZUM_B3="$CALISMA/cozum-b3.yml"
if compose_cozumle docker-compose.prod.yml "$ENV_SHA" "$COZUM_B3" \
   && compose_sozlesmesi_dogrula "$COZUM_B3" >/dev/null 2>&1; then
  B3_IMAJ="$(app_alani "$COZUM_B3" image)"
  if [ "$B3_IMAJ" = "${KANONIK_IMAJ}:${SHA_ETIKET}" ]; then
    yesil "B3 doğru repository + SHA etiketi → yeşil ($B3_IMAJ)"
  else
    kirmizi "B3 SHA etiketi çözümlenmedi: $B3_IMAJ"
  fi
else
  kirmizi "B3 doğru repository + SHA etiketi kapıdan geçmedi"
fi

# ---------------------------------------------------------------------------
baslik "C) Koşuya özel geçici manifest — paralel deploy izolasyonu"
# ---------------------------------------------------------------------------
# Deploy betiği source edilince akış çalışmaz, yalnız fonksiyonlar tanımlanır.
KOSU_YOLU="$CALISMA/kosu-yollari.txt"
: > "$KOSU_YOLU"
for i in 1 2; do
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh
    _gecici_alan_hazirla
    printf '%s\n' "$KURTARMA_MANIFEST" >> "$KOSU_YOLU"
    # Dosyayı gerçekten yaratıp bırak: iki koşu aynı yolu kullansaydı ikincisi
    # birincinin içeriğini ezerdi.
    printf 'kosu-%s\n' "$i" > "$KURTARMA_MANIFEST"
    printf '%s|%s\n' "$KURTARMA_MANIFEST" "$(cat "$KURTARMA_MANIFEST")" \
      >> "$CALISMA/icerik.txt"
    gecici_alan_temizle
  )
done
C1_A="$(sed -n 1p "$KOSU_YOLU")"; C1_B="$(sed -n 2p "$KOSU_YOLU")"
if [ -n "$C1_A" ] && [ -n "$C1_B" ] && [ "$C1_A" != "$C1_B" ]; then
  yesil "C1 paralel koşular farklı manifest dosyası kullanıyor"
else
  kirmizi "C1 iki koşu aynı manifest yolunu kullandı: [$C1_A] [$C1_B]"
fi
if grep -q "kosu-1" "$CALISMA/icerik.txt" && grep -q "kosu-2" "$CALISMA/icerik.txt"; then
  yesil "C2 koşular birbirinin manifest içeriğini ezmedi"
else
  kirmizi "C2 manifest içerikleri birbirini ezdi"
fi
if [ -e "$C1_A" ] || [ -e "$C1_B" ]; then
  kirmizi "C3 geçici dizin koşu sonunda temizlenmedi"
else
  yesil "C3 her koşu yalnız kendi geçici dizinini temizledi"
fi
# Sabit yol yalnız YORUMDA anılabilir. `file:line:` öneki soyulup satırın
# kendisi yorum mu diye bakılır; bu dosya kapsam dışıdır çünkü desenin kendisi
# burada literal olarak geçiyor.
C4_KOD="$(grep -n '/tmp/kurtarma' deploy/sunucu-deploy.sh deploy/surum-ve-kurtarma-testi.sh \
  deploy/veri-kaliciligi-kabul-testi.sh 2>/dev/null \
  | sed 's/^[^:]*:[0-9]*://' | grep -v '^[[:space:]]*#' || true)"
if [ -n "$C4_KOD" ]; then
  kirmizi "C4 sabit /tmp yolu hâlâ kodda kullanılıyor"
  printf '      %s\n' "$C4_KOD"
else
  yesil "C4 sabit /tmp yolu koddan kalkmış (yalnız yorumda anılıyor)"
fi

# ---------------------------------------------------------------------------
baslik "D) Mount envanteri — bilinmeyen kaynak fail-closed"
# ---------------------------------------------------------------------------
# `docker` stub'lanır: mount_denetimi yalnız `docker inspect --format` çağırır.
mount_senaryosu() {
  # Değişken adı bilerek `_mnt_veri`: `mount_denetimi` kendi çıktısını
  # `mount_satirlari` local'ine yazıyor ve stub içinden aynı adı okumak
  # `set -u` altında henüz atanmamış local'e düşerdi.
  local _mnt_veri="$1"
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh
    HACIM_ADI="harman-zamani_sungur_data"
    HACIM_KOKU="/opt/sungur-data"
    docker() { printf '%s' "$_mnt_veri"; }
    mount_denetimi "sahte-kap" >/dev/null 2>&1
  )
}

BEKLENEN_MOUNT='volume|harman-zamani_sungur_data|/opt/sungur-data|/var/lib/docker/volumes/x/_data
'
if mount_senaryosu "$BEKLENEN_MOUNT"; then
  yesil "D1 yalnız beklenen named volume → geçiyor"
else
  kirmizi "D1 beklenen mount reddedildi"
fi

if mount_senaryosu ""; then
  yesil "D2 hiç mount yok (kalıcı hacim öncesi kurulum) → geçiyor"
else
  kirmizi "D2 mount'suz konteyner reddedildi"
fi

BIND_MOUNT="${BEKLENEN_MOUNT}bind|-|/app/backend/data|/srv/eski-veri
"
if mount_senaryosu "$BIND_MOUNT"; then
  kirmizi "D3 bilinmeyen bind mount GEÇTİ (sahte yeşil)"
else
  yesil "D3 bilinmeyen bind mount → kırmızı"
fi

ANON_MOUNT="${BEKLENEN_MOUNT}volume|9f2c1ab34de5|/app/backend/data|/var/lib/docker/volumes/9f2c1ab34de5/_data
"
if mount_senaryosu "$ANON_MOUNT"; then
  kirmizi "D4 anonymous volume GEÇTİ (sahte yeşil)"
else
  yesil "D4 anonymous volume → kırmızı"
fi

FARKLI_NAMED="volume|baska_proje_sungur_data|/opt/sungur-data|/var/lib/docker/volumes/y/_data
"
if mount_senaryosu "$FARKLI_NAMED"; then
  kirmizi "D5 farklı named volume GEÇTİ (sahte yeşil)"
else
  yesil "D5 farklı adlı named volume → kırmızı"
fi

# Denetimin `pull`/`up` ÖNCESİNDE çağrıldığı, akış sırasından doğrulanır.
D6_MOUNT="$(grep -n 'mount_denetimi "\$APP_KABI"' deploy/sunucu-deploy.sh | head -1 | cut -d: -f1)"
# `compose pull app` DEĞİL `docker pull`: etiket artık .env.production'a
# doğrulamadan sonra yazıldığı için indirme compose'un değişken çözümünden
# ayrıldı. Desen değişirse D6 boş yakalar ve aşağıdaki -n kontrolü ısırır.
D6_PULL="$(grep -n '^docker pull "\$IMAJ_ADI' deploy/sunucu-deploy.sh | head -1 | cut -d: -f1)"
D6_UP="$(grep -n '"\${DC\[@\]}" up -d app' deploy/sunucu-deploy.sh | head -1 | cut -d: -f1)"
if [ -n "$D6_MOUNT" ] && [ -n "$D6_PULL" ] && [ -n "$D6_UP" ] \
   && [ "$D6_MOUNT" -lt "$D6_PULL" ] && [ "$D6_MOUNT" -lt "$D6_UP" ]; then
  yesil "D6 mount denetimi pull ($D6_PULL) ve up ($D6_UP) öncesinde ($D6_MOUNT)"
else
  kirmizi "D6 mount denetimi pull/up öncesinde değil (mount=$D6_MOUNT pull=$D6_PULL up=$D6_UP)"
fi

# ---------------------------------------------------------------------------
baslik "E) Yarım kalmış tohumlama — silmeden/üzerine yazmadan dur"
# ---------------------------------------------------------------------------
# `docker` stub'ı bütün çağrıları kaydeder ve işaret yoklamasına senaryoya göre
# yanıt verir. ÜÇ senaryo da AYNI üretim fonksiyonunu (`hacmi_tohumla`) çağırır;
# assertion kopyalanmaz. İşaret yoklaması, komut satırındaki `printf 'VAR:'`
# imzasından ayırt edilir — `hacim_manifesti` de `/vk:ro` bağladığı için mount
# yoluna bakmak iki çağrıyı karıştırırdı.
#
# `dolu`  : işaret var, içeriği yazılmış (klasik yarıda kesilme)
# `bos`   : işaret var, içeriği BOŞ (dosya yaratıldı, yazım kesildi)
# `hata`  : yoklama hiç çalışmadı (daemon hatası) — bilinmeyen durum
tohumlama_senaryosu() {
  local mod="$1" log="$2"
  : > "$log"
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh
    HACIM_ADI="test_vol"
    _gecici_alan_hazirla
    printf 'hash 12 ./a.jpg\n' > "$KURTARMA_MANIFEST"
    docker() {
      printf '%s\n' "$*" >> "$log"
      case "$*" in
        *"printf 'VAR:'"*)
          case "$mod" in
            dolu) printf 'VAR:baslangic=2026-08-09T00:00:00Z pid=1 kaynak=/x' ;;
            bos)  printf 'VAR:' ;;
            hata) return 3 ;;
          esac
          ;;
        *) : ;;
      esac
    }
    mkdir -p "$CALISMA/kurtarma/data"
    hacmi_tohumla "$CALISMA/kurtarma" "imaj:1" >/dev/null 2>&1
  )
}

E_LOG="$CALISMA/docker-cagrilari.txt"
E_RC=0
tohumlama_senaryosu dolu "$E_LOG" || E_RC=$?
if [ "$E_RC" -ne 0 ]; then
  yesil "E1 dolu işaret → kırmızı (exit $E_RC)"
else
  kirmizi "E1 dolu işaret GEÇTİ (sahte yeşil)"
fi

# BOŞ işaret dosyası da durdurmalı. İçerik tabanlı denetim (`[ -n "$(cat ...)" ]`)
# bu hâli GÖRMEZDEN gelir: dosya orada durur ama `cat` boş döner ve yarım kalmış
# tohumlamanın üstüne kopyalama yapılırdı.
E_LOG_BOS="$CALISMA/docker-cagrilari-bos.txt"
E_BOS_RC=0
tohumlama_senaryosu bos "$E_LOG_BOS" || E_BOS_RC=$?
if [ "$E_BOS_RC" -ne 0 ]; then
  yesil "E1b BOŞ işaret dosyası da → kırmızı (exit $E_BOS_RC)"
else
  kirmizi "E1b boş işaret dosyası GEÇTİ (sahte yeşil — içerik tabanlı denetime dönülmüş)"
fi

# Yoklama hiç çalışmazsa "işaret yok" varsaymak, korunmaya çalışılan hâlde
# deploy'u sürdürmek olurdu. Bilinmeyen durum da fail-closed.
E_LOG_HATA="$CALISMA/docker-cagrilari-hata.txt"
E_HATA_RC=0
tohumlama_senaryosu hata "$E_LOG_HATA" || E_HATA_RC=$?
if [ "$E_HATA_RC" -ne 0 ]; then
  yesil "E1c işaret yoklaması başarısızsa → kırmızı (exit $E_HATA_RC)"
else
  kirmizi "E1c okunamayan işaret yoklaması GEÇTİ (sahte yeşil)"
fi

# E2/E3, üç senaryonun ÜÇÜNDE de hiçbir yazma/silme denenmediğini ister.
E_HEPSI="$CALISMA/docker-cagrilari-hepsi.txt"
cat "$E_LOG" "$E_LOG_BOS" "$E_LOG_HATA" > "$E_HEPSI"
if grep -q "cp -an" "$E_HEPSI"; then
  kirmizi "E2 yarım hâlde kopyalama denendi (üzerine yazma riski)"
else
  yesil "E2 yarım hâlde kopyalama denenmedi (dolu/boş/okunamadı)"
fi
if grep -qE "rm -f /hedef/|rm -rf" "$E_HEPSI"; then
  kirmizi "E3 yarım hâlde silme denendi"
else
  yesil "E3 yarım hâlde hiçbir dosya silinmedi (dolu/boş/okunamadı)"
fi

# Manifest bu koşuda üretilmemişse tohumlama hiç başlamamalı.
E4_RC=0
(
  export SUNGUR_DEPLOY_KAYNAK=evet
  # shellcheck source=/dev/null
  . ./deploy/sunucu-deploy.sh
  docker() { :; }
  hacmi_tohumla "$CALISMA/kurtarma" "imaj:1" >/dev/null 2>&1
) || E4_RC=$?
if [ "$E4_RC" -ne 0 ]; then
  yesil "E4 koşuya ait manifest yoksa tohumlama başlamıyor (exit $E4_RC)"
else
  kirmizi "E4 manifestsiz tohumlama GEÇTİ (sahte yeşil)"
fi

# İşaret dosyası manifestlerin dışında tutulmalı; yoksa hash karşılaştırmaları
# bozulur ve C3/D7/E4 gibi eşitlik testleri sahte kırmızı verirdi.
if grep -q '! -name ".sungur-tohumlama-devam-ediyor"' deploy/sunucu-deploy.sh; then
  yesil "E5 tohumlama işareti manifest dışında bırakılıyor"
else
  kirmizi "E5 tohumlama işareti manifeste sızıyor"
fi

# ---------------------------------------------------------------------------
baslik "F) Deploy betiğinin IMAJ_ADI'sı — sözleşmenin üçüncü katmanı"
# ---------------------------------------------------------------------------
# Kanonik depo üç yerde yaşıyor: CI yayın hedefi, compose `app.image` ve
# `deploy/sunucu-deploy.sh::IMAJ_ADI`. İlk ikisi A/B bölümlerinde kilitli;
# üçüncüsü hiçbir kapı tarafından görülmüyordu. IMAJ_ADI geri kayarsa deploy
# fail-closed durur (veri kaybı yok) ama teşhis yanıltıcı olur: `docker image
# inspect` boş döner ve betik "OCI revision etiketi yok" der — operatör imajı
# değil, depoyu aramalıyken etiketi aramaya başlar.
#
# Kapı METNE değil, betik source edildiğinde ortaya çıkan GERÇEK değere bakar.
# `SUNGUR_IMAJ_ADI` bilinçli olarak unset edilir; kapı varsayılanı ölçmeli,
# ortamdan gelen geçici bir değeri değil.
imaj_adi_dogrula() {
  local betik="$1" deger
  deger="$(
    export SUNGUR_DEPLOY_KAYNAK=evet
    unset SUNGUR_IMAJ_ADI
    # shellcheck source=/dev/null
    . "$betik" >/dev/null 2>&1 || exit 9
    printf '%s' "${IMAJ_ADI:-}"
  )" || return 1
  if [ "$deger" != "$KANONIK_IMAJ" ]; then
    echo "HATA: sunucu-deploy.sh IMAJ_ADI kanonik depoda değil: '$deger' (beklenen '$KANONIK_IMAJ')" >&2
    return 1
  fi
}

if imaj_adi_dogrula ./deploy/sunucu-deploy.sh; then
  yesil "F1 sunucu-deploy.sh IMAJ_ADI kanonik ($KANONIK_IMAJ)"
else
  kirmizi "F1 sunucu-deploy.sh IMAJ_ADI kanonik değil"
fi

# Negatif mutasyon: depo adı eski hâline döndürülürse kapı ısırmalı.
IMAJ_MUTANT="$CALISMA/sunucu-deploy-yanlis-imaj.sh"
sed "s|$KANONIK_IMAJ|ghcr.io/finarfins/sungur-tarim-erp|g" \
  deploy/sunucu-deploy.sh > "$IMAJ_MUTANT"
if cmp -s deploy/sunucu-deploy.sh "$IMAJ_MUTANT"; then
  # Mutasyon hiç uygulanmadıysa F2 bir şey ölçmüyor demektir. Sessizce
  # "kapı ısırdı" demek yerine bunu ayrı bir kırmızı olarak bildiriyoruz.
  kirmizi "F2 mutasyon uygulanamadı (betikte '$KANONIK_IMAJ' geçmiyor) — kapı ölçülemedi"
elif imaj_adi_dogrula "$IMAJ_MUTANT" 2>/dev/null; then
  # stderr bilinçli olarak yutuluyor: buradaki hata mesajı BEKLENEN sonuçtur,
  # CI logunda gerçek bir arıza gibi görünmemeli.
  kirmizi "F2 yanlış IMAJ_ADI kapıdan GEÇTİ (sahte yeşil)"
else
  yesil "F2 yanlış IMAJ_ADI → kırmızı"
fi

# Üç katmanın aynı adı gösterdiği tek yerde toplanır: compose'dan çözümlenen
# depo ile deploy betiğinin depoları birebir eşit olmalı.
F3_COMPOSE_DEPO="${A1_IMAJ%:*}"
F3_BETIK_DEPO="$(
  export SUNGUR_DEPLOY_KAYNAK=evet
  unset SUNGUR_IMAJ_ADI
  # shellcheck source=/dev/null
  . ./deploy/sunucu-deploy.sh >/dev/null 2>&1 || exit 9
  printf '%s' "${IMAJ_ADI:-}"
)" || F3_BETIK_DEPO=""
if [ -n "$F3_COMPOSE_DEPO" ] && [ "$F3_COMPOSE_DEPO" = "$F3_BETIK_DEPO" ]; then
  yesil "F3 compose ve deploy betiği aynı depoyu gösteriyor ($F3_COMPOSE_DEPO)"
else
  kirmizi "F3 katmanlar ayrışmış — compose: '$F3_COMPOSE_DEPO', betik: '$F3_BETIK_DEPO'"
fi

# ---------------------------------------------------------------------------
baslik "G) Harman Zamanı kurulum kimliği ve tek alan adı sözleşmesi"
# ---------------------------------------------------------------------------
# Betiğin varsayılanları HEDEF KURULUMU göstermeli. Yanlış kök/proje adıyla
# çalıştırılan bir deploy ya olmayan dizine `cd` eder ya da mevcut stack'i
# güncellemek yerine PARALEL bir stack yaratır — ikincisi sessizdir ve iki
# uygulama aynı RDS'e yazmaya başlar.
kurulum_kimligi() {
  local betik="$1"
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    unset SUNGUR_KOK SUNGUR_PROJE SUNGUR_APP_KABI SUNGUR_HACIM
    # shellcheck source=/dev/null
    . "$betik" >/dev/null 2>&1 || exit 9
    printf '%s|%s|%s' "$KOK" "$PROJE" "$APP_KABI"
  )
}
G1="$(kurulum_kimligi ./deploy/sunucu-deploy.sh)"
if [ "$G1" = "/opt/harman-zamani|harman-zamani|harman-zamani-app-1" ]; then
  yesil "G1 varsayılanlar hedef kuruluma işaret ediyor ($G1)"
else
  kirmizi "G1 varsayılanlar yanlış: $G1"
fi

# G2 — mutasyon. BOŞ DÖNÜŞ BAŞARI SAYILMAZ. Önceki sürüm yalnız "kanonik
# kimliğe eşit değil" diye bakıyordu; probe sourcing hatasıyla boş dönseydi
# (exit 9) bu koşul da sağlanır ve test yeşil kalırdı — yani kapıyı hiç
# ölçmeden geçerdi. Artık mutantın TAM OLARAK mutasyona uğramış kimliği
# döndürmesi bekleniyor: hem probe'un çalıştığı hem guard'ın değiştiği kanıtlanır.
BEKLENEN_KIMLIK="/opt/harman-zamani|harman-zamani|harman-zamani-app-1"
MUTANT_KIMLIK="/opt/sungur-tarim-erp|sungur-tarim-erp|sungur-tarim-erp-app-1"
KURULUM_MUTANT="$CALISMA/sunucu-deploy-yanlis-kurulum.sh"
sed 's|/opt/harman-zamani|/opt/sungur-tarim-erp|; s|SUNGUR_PROJE:-harman-zamani|SUNGUR_PROJE:-sungur-tarim-erp|' \
  deploy/sunucu-deploy.sh > "$KURULUM_MUTANT"
G2_KIMLIK="$(kurulum_kimligi "$KURULUM_MUTANT")"
if cmp -s deploy/sunucu-deploy.sh "$KURULUM_MUTANT"; then
  kirmizi "G2 mutasyon uygulanamadı — kapı ölçülemedi"
elif [ -z "$G2_KIMLIK" ]; then
  kirmizi "G2 probe BOŞ döndü — kapı ölçülemedi (mutant source edilemiyor olabilir)"
elif [ "$G2_KIMLIK" = "$BEKLENEN_KIMLIK" ]; then
  kirmizi "G2 yanlış kurulum kimliği kapıdan GEÇTİ (sahte yeşil)"
elif [ "$G2_KIMLIK" != "$MUTANT_KIMLIK" ]; then
  kirmizi "G2 mutant beklenmeyen kimlik verdi: '$G2_KIMLIK' (beklenen '$MUTANT_KIMLIK')"
else
  yesil "G2 yanlış kök/proje adı → kırmızı ($G2_KIMLIK)"
fi

# Üretim çözümünde `db` servisi BULUNMAMALI: veritabanı harici RDS.
G3_SERVISLER="$(python3 - "$COZUM" <<'PY'
import sys, json
print(",".join(sorted((json.load(open(sys.argv[1])).get("services") or {}))))
PY
)"
if [ "$G3_SERVISLER" = "app,proxy" ]; then
  yesil "G3 üretim çözümünde yalnız app+proxy (db yok — harici RDS)"
else
  kirmizi "G3 üretim çözümünde beklenmeyen servisler: $G3_SERVISLER"
fi

G4_DEPENDS="$(app_alani "$COZUM" depends_on)"
if [ "$G4_DEPENDS" = "<YOK>" ]; then
  yesil "G4 app.depends_on düşürülmüş (olmayan db beklenmeyecek)"
else
  kirmizi "G4 app.depends_on hâlâ var: $G4_DEPENDS — up -d asılırdı"
fi

# `gate` profili açıkken db GERİ GELMELİ; yoksa yerel PostgreSQL testleri
# çalışamaz. Profil, servisi silmenin değil ertelemenin yolu.
COZUM_GATE="$CALISMA/cozum-gate.json"
if docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file "$ENV_DOSYA" \
     --profile gate config --format json > "$COZUM_GATE" 2>/dev/null \
   && python3 -c "import json,sys; s=json.load(open(sys.argv[1]))['services']; sys.exit(0 if 'db' in s and 'gate' in s else 1)" "$COZUM_GATE"; then
  yesil "G5 --profile gate açıkken db geri geliyor"
else
  kirmizi "G5 gate profilinde db yok — yerel PostgreSQL testleri kırılır"
fi

# Tek alan adı Caddy sözleşmesi.
if grep -qE '^\{\$APP_DOMAIN\}' deploy/Caddyfile \
   && grep -qE '^www\.\{\$APP_DOMAIN\}' deploy/Caddyfile \
   && grep -q 'root \* /srv/bakim' deploy/Caddyfile; then
  yesil "G6 Caddyfile: apex + www→apex + /srv/bakim bakım sayfası"
else
  kirmizi "G6 Caddyfile tek alan adı sözleşmesini karşılamıyor"
fi

# Yalnız AKTİF yapılandırma sayılır; yorumda anılması serbest — kaldırılma
# gerekçesini yazan yorumun kapıyı kırması saçma olurdu (C4 ile aynı desen).
# `file:satır:` öneki soyulup satırın kendisi yorum mu diye bakılır.
G7_ESKI="$(grep -nE 'MARKETING_DOMAIN|LEGACY_DOMAIN' deploy/Caddyfile .env.production.example \
             .github/workflows/ci.yml docker-compose.prod.yml 2>/dev/null \
           | sed -E 's/^[^:]*:[0-9]+://' | grep -vE '^[[:space:]]*#' || true)"
if [ -z "$G7_ESKI" ]; then
  yesil "G7 MARKETING_DOMAIN/LEGACY_DOMAIN aktif yapılandırmadan kalkmış"
else
  kirmizi "G7 eski çok-alan-adı değişkenleri hâlâ aktif:"
  printf '      %s\n' "$G7_ESKI"
fi

# ---------------------------------------------------------------------------
baslik "H) Yedek, etiket ve geri dönüş kapıları"
# ---------------------------------------------------------------------------
# Etiket sözleşmesi: yalnız 40 haneli SHA.
etiket_senaryosu() {
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh >/dev/null 2>&1 || exit 9
    etiket_dogrula "$1" >/dev/null 2>&1
  )
}
H_OK=0
for gecersiz in "" "develop" "latest" "22686b5" "main" "ZZ686b5205ec39ad05e62537cc7a72c0ae92a606"; do
  etiket_senaryosu "$gecersiz" && { kirmizi "H1 geçersiz etiket '$gecersiz' KABUL edildi"; H_OK=1; }
done
[ "$H_OK" -eq 0 ] && yesil "H1 hareketli/eksik/bozuk etiketlerin hepsi reddedildi"
if etiket_senaryosu "22686b5205ec39ad05e62537cc7a72c0ae92a606"; then
  yesil "H2 geçerli 40 haneli SHA kabul edildi"
else
  kirmizi "H2 geçerli SHA reddedildi"
fi

# Yedek kapısı: pg_dump başarısız / dosya küçük / arşiv okunamaz → fail-closed.
# `docker` stub'lanır; gerçek `veritabani_yedegi_al` çalışır.
yedek_senaryosu() {
  local mod="$1" log="$2"
  : > "$log"
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh >/dev/null 2>&1 || exit 9
    cd "$CALISMA/sahte-kok" || exit 9
    _m="$mod"; _log="$log"
    docker() {
      printf '%s\n' "$*" >> "$_log"
      case "$*" in
        *pg_restore*) [ "$_m" = "bozuk-arsiv" ] && return 1; return 0 ;;
        *pg_dump*)
          case "$_m" in
            pg_dump-hata) return 5 ;;
            kucuk)        printf 'kisa' ;;
            *)            head -c 4096 /dev/zero | tr '\0' 'X' ;;
          esac ;;
        *) : ;;
      esac
    }
    veritabani_yedegi_al "$CALISMA/yedek-$_m.dump" >/dev/null 2>&1
  )
}
mkdir -p "$CALISMA/sahte-kok"
printf 'DATABASE_URL=postgresql+psycopg://kullanici:parola@ornek.rds.amazonaws.com:5432/db\n' \
  > "$CALISMA/sahte-kok/.env.production"
for mod in pg_dump-hata kucuk bozuk-arsiv; do
  if yedek_senaryosu "$mod" "$CALISMA/yedek-log-$mod"; then
    kirmizi "H3/$mod yedek kapısı GEÇTİ (sahte yeşil)"
  else
    yesil "H3/$mod → fail-closed"
  fi
done
if yedek_senaryosu "iyi" "$CALISMA/yedek-log-iyi"; then
  yesil "H4 sağlıklı yedek → geçiyor"
else
  kirmizi "H4 sağlıklı yedek reddedildi"
fi

# DATABASE_URL komut satırına YAZILMAMALI: `ps` ve kabuk geçmişi sızdırır.
if grep -qE 'parola|ornek\.rds\.amazonaws\.com' "$CALISMA/yedek-log-iyi"; then
  kirmizi "H5 bağlantı dizesi docker komut satırına sızdı"
else
  yesil "H5 bağlantı dizesi komut satırında görünmüyor (yalnız -e PGURL)"
fi

# Yedek yolu artık olmayan `db` servisine bağlı OLMAMALI.
if grep -qE '"\$\{DC\[@\]\}" exec -T db' deploy/sunucu-deploy.sh; then
  kirmizi "H6 yedek hâlâ compose 'db' servisine bağlı — bu sunucuda db yok"
else
  yesil "H6 yedek harici RDS yolundan alınıyor"
fi

# Geri dönüş işareti. DÖRT DAL AYRI AYRI sınanır. Önceki sürümde tek senaryo
# vardı ve etiketi "APP_IMAGE_TAG yoksa" diyordu; oysa stub `docker inspect`
# için boş çıktı veriyordu, fonksiyon daha "imaj kimliği okunamadı" dalında
# duruyor ve iddia edilen dal HİÇ çalışmıyordu. Doğru sebeple geçmeyen bir
# assertion, geçmeyen bir assertion'dır.
mkdir -p "$CALISMA/kok-etiketli"
printf 'DATABASE_URL=postgresql://u:p@h:5432/d\nAPP_IMAGE_TAG=22686b5205ec39ad05e62537cc7a72c0ae92a606\n' \
  > "$CALISMA/kok-etiketli/.env.production"

rollback_senaryosu() {
  local mod="$1" kok="$2" revision="${3:-22686b5205ec39ad05e62537cc7a72c0ae92a606}" log="${4:-/dev/null}"
  local home="${5:-$kok}" devam_isareti="${6:-}"
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh >/dev/null 2>&1 || exit 9
    cd "$kok" || exit 9
    _m="$mod" _revision="$revision"
    docker() {
      if [ "${1:-}" = "inspect" ] && [ "${2:-}" = "kap" ]; then
        if [ "${3:-}" = "--format" ]; then
          [ "$_m" = "imaj-kimligi-yok" ] && return 0
          printf 'sha256:abcd\n'
          return 0
        fi
        [ "$_m" = "kap-yok" ] && return 1
        return 0
      fi
      if [ "${1:-} ${2:-}" = "image inspect" ]; then
        case "${5:-}" in
          *org.opencontainers.image.revision*)
            case "$_m" in revision-yok*) return 0 ;; esac
            [ "$_m" = "revision-bozuk" ] && { printf 'develop\n'; return 0; }
            printf '%s\n' "$_revision"
            ;;
          *'{{.Id}}'*)
            [ "$_m" = "revision-yok-env-farkli" ] && { printf 'sha256:farkli\n'; return 0; }
            printf 'sha256:abcd\n'
            ;;
          *) printf 'ghcr.io/finarfins/nazgul@sha256:dddd\n' ;;
        esac
        return 0
      fi
      return 0
    }
    printf() {
      if [ "$_m" = "tag-yazma-hata" ]; then
        case "${1:-}|${2:-}" in
          '%s\n|APP_IMAGE_TAG='*|'APP_IMAGE_TAG=%s\n|'*) return 1 ;;
        esac
      fi
      builtin printf "$@"
    }
    HOME="$home" geri_donus_isaretini_yaz kap || exit 1
    [ -z "$devam_isareti" ] || printf 'DEPLOY_DEVAM\n' > "$devam_isareti"
  ) >"$log" 2>&1
}

# H7a — konteyner yok: ilk kurulum, geçmeli ve ILK_KURULUM yazmalı.
if rollback_senaryosu kap-yok "$CALISMA/kok-etiketli" \
   && grep -q ILK_KURULUM "$CALISMA/kok-etiketli/.ROLLBACK_TAG" 2>/dev/null; then
  yesil "H7a konteyner yokken ilk kurulum olarak işaretleniyor"
else
  kirmizi "H7a ilk kurulum dalı beklendiği gibi davranmadı"
fi

# H7b — konteyner var ama imaj kimliği okunamıyor: fail-closed.
printf 'APP_IMAGE_TAG=bayat\n' > "$CALISMA/kok-etiketli/.ROLLBACK_TAG"
if rollback_senaryosu imaj-kimligi-yok "$CALISMA/kok-etiketli"; then
  kirmizi "H7b okunamayan imaj kimliği GEÇTİ (sahte yeşil)"
elif [ -e "$CALISMA/kok-etiketli/.ROLLBACK_TAG" ]; then
  kirmizi "H7b okunamayan imaj kimliği dalında bayat .ROLLBACK_TAG kaldı"
else
  yesil "H7b imaj kimliği okunamıyorsa duruyor ve bayat TAG'i siliyor"
fi

# H7c — ağaçtaki env ile çalışan imaj ayrışsa da çalışan OCI revision yazılmalı.
H7C_KOK="$CALISMA/rb-ayrisik"
H7C_ENV_SHA="1111111111111111111111111111111111111111"
H7C_CALISAN_SHA="2222222222222222222222222222222222222222"
mkdir -p "$H7C_KOK"
printf 'APP_IMAGE_TAG=%s\n' "$H7C_ENV_SHA" > "$H7C_KOK/.env.production"
if rollback_senaryosu tam "$H7C_KOK" "$H7C_CALISAN_SHA" \
   && grep -qxF "APP_IMAGE_TAG=$H7C_CALISAN_SHA" "$H7C_KOK/.ROLLBACK_TAG" \
   && ! grep -qF "$H7C_ENV_SHA" "$H7C_KOK/.ROLLBACK_TAG" \
   && grep -qF 'sha256:abcd' "$H7C_KOK/.ROLLBACK_IMAGE" \
   && grep -qF 'sha256:dddd' "$H7C_KOK/.ROLLBACK_DIGEST"; then
  yesil "H7c env/çalışan imaj ayrışınca TAG çalışan OCI revision'ı gösteriyor"
else
  kirmizi "H7c .ROLLBACK_TAG ağaçtaki yanlış APP_IMAGE_TAG'den üretildi"
fi

# H7d — revision yok ama env etiketi aynı yerel image ID'yi gösteriyor: güvenli fallback.
H7D_LOG="$CALISMA/rb-env-dogrulandi.log"
if rollback_senaryosu revision-yok "$CALISMA/kok-etiketli" ignored "$H7D_LOG" \
   && grep -q '22686b5205ec39ad05e62537cc7a72c0ae92a606' "$CALISMA/kok-etiketli/.ROLLBACK_TAG" 2>/dev/null \
   && grep -q 'sha256:abcd' "$CALISMA/kok-etiketli/.ROLLBACK_IMAGE" 2>/dev/null \
   && grep -q 'sha256:dddd' "$CALISMA/kok-etiketli/.ROLLBACK_DIGEST" 2>/dev/null \
   && grep -qF "çalışan image ID ile doğrulanarak .env.production'dan türetildi" "$H7D_LOG"; then
  yesil "H7d revision yoksa eşleşen image ID ile env TAG doğrulanarak yazılıyor"
else
  kirmizi "H7d doğrulanmış env fallback sözleşmesi çalışmadı"
fi

# H7e — env SHA geçerli olsa bile etiketli image ID çalışan imajdan farklıysa durmalı.
H7E_KOK="$CALISMA/rb-env-ayrisik"
H7E_LOG="$CALISMA/rb-env-ayrisik.log"
mkdir -p "$H7E_KOK"
printf 'APP_IMAGE_TAG=%s\n' "$H7C_ENV_SHA" > "$H7E_KOK/.env.production"
printf 'APP_IMAGE_TAG=bayat\n' > "$H7E_KOK/.ROLLBACK_TAG"
if rollback_senaryosu revision-yok-env-farkli "$H7E_KOK" ignored "$H7E_LOG"; then
  kirmizi "H7e ayrışık env image ID ile deploy DEVAM etti"
elif [ -e "$H7E_KOK/.ROLLBACK_TAG" ]; then
  kirmizi "H7e duruşta bayat .ROLLBACK_TAG kaldı"
elif ! grep -qF 'sha256:abcd' "$H7E_KOK/.ROLLBACK_IMAGE" \
  || ! grep -qF 'sha256:dddd' "$H7E_KOK/.ROLLBACK_DIGEST"; then
  kirmizi "H7e duruşta IMAGE/DIGEST tanısı yazılmadı"
else
  yesil "H7e env image ID ayrışırsa duruyor; stale TAG siliniyor, IMAGE+DIGEST kalıyor"
fi

# H7f — revision yokken env etiketi geçersiz veya yoksa iki durumda da durmalı.
H7F_HATA=0
for cift in 'gecersiz|develop' 'yok|__yok__'; do
  ad="${cift%%|*}"; deger="${cift#*|}"; kok="$CALISMA/rb-env-$ad"
  mkdir -p "$kok"
  printf 'DATABASE_URL=postgresql://u:p@h:5432/d\n' > "$kok/.env.production"
  [ "$deger" = "__yok__" ] || printf 'APP_IMAGE_TAG=%s\n' "$deger" >> "$kok/.env.production"
  printf 'APP_IMAGE_TAG=bayat\n' > "$kok/.ROLLBACK_TAG"
  if rollback_senaryosu revision-yok "$kok" ignored; then
    kirmizi "H7f/$ad doğrulanamayan rollback etiketiyle deploy DEVAM etti"
    H7F_HATA=1
  elif [ -e "$kok/.ROLLBACK_TAG" ] \
    || ! grep -qF 'sha256:abcd' "$kok/.ROLLBACK_IMAGE" \
    || ! grep -qF 'sha256:dddd' "$kok/.ROLLBACK_DIGEST"; then
    kirmizi "H7f/$ad duruş tanıları veya stale TAG temizliği hatalı"
    H7F_HATA=1
  fi
done
[ "$H7F_HATA" -eq 0 ] && yesil "H7f revision yok + env geçersiz/yok → duruyor; TAG siliniyor, IMAGE+DIGEST kalıyor"

# H7g — gerçek çağrı `fonksiyon || exit 1` biçiminde olsa bile dosya I/O
# hatası açık return ile deploy'u durdurmalı; set -e'ye güvenilmemelidir.
H7G_KOK="$CALISMA/rb-io-hata"
H7G_LOG="$CALISMA/rb-io-hata.log"
H7G_DEVAM="$CALISMA/rb-io-hata-deploy-devam"
mkdir -p "$H7G_KOK"
if rollback_senaryosu tam "$H7G_KOK" 22686b5205ec39ad05e62537cc7a72c0ae92a606 \
   "$H7G_LOG" "$H7G_KOK/var-olmayan/alt-dizin" "$H7G_DEVAM"; then
  kirmizi "H7g yazılamayan rollback dosyalarıyla deploy DEVAM etti"
elif [ -e "$H7G_DEVAM" ]; then
  kirmizi "H7g rollback I/O hatasından sonra 4/7 devam işareti oluştu"
elif ! grep -qF 'rollback image kimliği yazılamadı' "$H7G_LOG"; then
  kirmizi "H7g I/O hatası doğru kapıda raporlanmadı"
else
  yesil "H7g rollback I/O hatası non-zero döndü ve deploy 4/7'ye ilerlemedi"
fi

H7G_TAG_KOK="$CALISMA/rb-tag-yazma-hata"
H7G_TAG_LOG="$CALISMA/rb-tag-yazma-hata.log"
mkdir -p "$H7G_TAG_KOK"
if rollback_senaryosu tag-yazma-hata "$H7G_TAG_KOK" \
   22686b5205ec39ad05e62537cc7a72c0ae92a606 "$H7G_TAG_LOG"; then
  kirmizi "H7g/TAG yazma hatasıyla deploy DEVAM etti"
elif [ -e "$H7G_TAG_KOK/.ROLLBACK_TAG" ]; then
  kirmizi "H7g/TAG yazma hatası final .ROLLBACK_TAG bıraktı"
else
  yesil "H7g/TAG yazma hatası non-zero döndü ve final .ROLLBACK_TAG bırakmadı"
fi

# Digest kapısı: verilirse birebir eşleşmeli.
digest_senaryosu() {
  (
    export SUNGUR_DEPLOY_KAYNAK=evet
    # shellcheck source=/dev/null
    . ./deploy/sunucu-deploy.sh >/dev/null 2>&1 || exit 9
    _d="$1"
    docker() { case "$*" in *"image inspect"*) printf 'ghcr.io/x/y@sha256:aaaa\n' ;; *) : ;; esac; }
    digest_dogrula imaj:1 "$_d" >/dev/null 2>&1
  )
}
if digest_senaryosu "sha256:bbbb"; then
  kirmizi "H8 yanlış digest kapıdan GEÇTİ (sahte yeşil)"
else
  yesil "H8 yanlış digest → kırmızı"
fi
if digest_senaryosu "sha256:aaaa"; then
  yesil "H9 doğru digest → geçiyor"
else
  kirmizi "H9 doğru digest reddedildi"
fi

# Pull kapısı kendi katmanında durmalı. Sonraki digest/sürüm kapısında düşmek
# yeterli değildir: pull hatasından sonra 5/7'ye geçilirse bu guard değildir.
H10_SHA="22686b5205ec39ad05e62537cc7a72c0ae92a606"
H10_KOK="$CALISMA/h10-pull"; H10_BIN="$CALISMA/h10-bin"; H10_CIKTI="$CALISMA/h10.log"
mkdir -p "$H10_KOK/deploy" "$H10_BIN"
cp docker-compose.yml docker-compose.prod.yml "$H10_KOK/"
cp .env.production.example "$H10_KOK/.env.production"
cat >> "$H10_KOK/.env.production" <<EOF
POSTGRES_PASSWORD=h10-parolasi-yeterince-uzun
TURNSTILE_SECRET_KEY=h10-dummy
BOOTSTRAP_ADMIN_PASSWORD=h10-dummy
APP_IMAGE_TAG=1111111111111111111111111111111111111111
EOF
cp deploy/sunucu-deploy.sh "$H10_KOK/deploy/"
printf '%s\n' "$H10_SHA" > "$H10_KOK/deploy/RELEASE_SHA"
cat > "$H10_BIN/docker" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  info*)                                        exit 0 ;;
  "inspect "*--format*ReadonlyRootfs*)         echo true; exit 0 ;;
  "inspect "*--format*Mounts*)                 printf 'volume|harman-zamani_sungur_data|/opt/sungur-data|/x\n'; exit 0 ;;
  "inspect "*--format*Image*)                  echo 'sha256:eski'; exit 0 ;;
  "inspect "*)                                 exit 0 ;;
  "image inspect"*RepoDigests*)                echo 'ghcr.io/finarfins/nazgul@sha256:aaaa'; exit 0 ;;
  "image inspect"*)                            echo 'sha256:eski'; exit 0 ;;
  run*pg_dump*)                                 head -c 4096 /dev/zero | tr '\0' 'X'; exit 0 ;;
  run*pg_restore*)                              exit 0 ;;
  pull*)                                        echo 'manifest unknown' >&2; exit 23 ;;
  *)                                            exit 0 ;;
esac
STUB
chmod +x "$H10_BIN/docker"
H10_ONCE="$(sha256sum "$H10_KOK/.env.production" | cut -d' ' -f1)"
H10_RC=0
( cd "$H10_KOK" && PATH="$H10_BIN:$PATH" SUNGUR_KOK="$H10_KOK" \
    SUNGUR_PROJE=harman-zamani HOME="$H10_KOK" \
    bash deploy/sunucu-deploy.sh "$H10_SHA" ) >"$H10_CIKTI" 2>&1 || H10_RC=$?
H10_SONRA="$(sha256sum "$H10_KOK/.env.production" | cut -d' ' -f1)"
H10_GUARD_SAYISI="$(grep -cE '^[[:space:]]*docker pull "\$IMAJ_ADI:\$ETIKET" \|\| exit 1[[:space:]]*$' \
  "$H10_KOK/deploy/sunucu-deploy.sh" || true)"
if [ "$H10_GUARD_SAYISI" -eq 1 ] && [ "$H10_RC" -ne 0 ] && [ "$H10_ONCE" = "$H10_SONRA" ] \
   && ! grep -qF '== 5/7 İmaj ↔ yapılandırma sözleşmesi ==' "$H10_CIKTI"; then
  yesil "H10 pull hatası kendi kapısında durdu; .env.production SHA-256 değişmedi (exit $H10_RC)"
else
  kirmizi "H10 açık pull guard'ı yok, akış kendi kapısında durmadı veya .env.production değişti"
fi

# ---------------------------------------------------------------------------
baslik "I) Tam akış — başarısız doğrulama .env.production'a DOKUNMAMALI"
# ---------------------------------------------------------------------------
# Buradaki testler betiği SOURCE ETMEZ; gerçek akışı uçtan uca çalıştırır.
# `docker` PATH üzerinden stub'lanır, sahte bir SUNGUR_KOK verilir ve
# .env.production'ın sha256'sı çalıştırmadan önce/sonra karşılaştırılır.
#
# Neden gerekti: etiket eskiden 4/7'nin BAŞINDA yazılıyordu. 5/7 (digest veya
# sürüm sözleşmesi) düştüğünde deploy duruyor, ama dosya DOĞRULANMAMIŞ etiketi
# göstermeye devam ediyordu — sonraki herhangi bir `up -d` o imajı canlıya
# alırdı. Kapı, arkasında açık kalan bir kapı bıraktığı sürece kapı değildir.
SHA_GECERLI="22686b5205ec39ad05e62537cc7a72c0ae92a606"

akis_koku_hazirla() {
  local kok="$1"
  rm -rf "$kok"; mkdir -p "$kok/deploy"
  cp docker-compose.yml docker-compose.prod.yml "$kok/"
  cp .env.production.example "$kok/.env.production"
  cat >> "$kok/.env.production" <<EOF
POSTGRES_PASSWORD=akis-testi-parolasi-yeterince-uzun
TURNSTILE_SECRET_KEY=akis-dummy
BOOTSTRAP_ADMIN_PASSWORD=akis-dummy
APP_IMAGE_TAG=1111111111111111111111111111111111111111
EOF
  cp deploy/sunucu-deploy.sh "$kok/deploy/"
  printf '%s\n' "$SHA_GECERLI" > "$kok/deploy/RELEASE_SHA"
}

# Stub `docker`: akışın her çağrısına makul yanıt verir, `MOD`a göre TEK bir
# noktada arızalanır. Gerçek betik başka hiçbir yerde takılmamalı.
akis_stub_yaz() {
  local bin="$1"
  mkdir -p "$bin"
  cat > "$bin/docker" <<'STUB'
#!/usr/bin/env bash
hepsi="$*"
case "$hepsi" in
  info*)               exit 0 ;;
  "inspect "*--format*'{{.HostConfig.ReadonlyRootfs}}'*) echo true; exit 0 ;;
  "inspect "*--format*Mounts*) printf 'volume|harman-zamani_sungur_data|/opt/sungur-data|/x\n'; exit 0 ;;
  "inspect "*--format*'{{.Image}}'*) echo "sha256:eski"; exit 0 ;;
  "inspect "*)         exit 0 ;;
  "image inspect"*RepoDigests*) echo "ghcr.io/finarfins/nazgul@${GERCEK_DIGEST:-sha256:aaaa}"; exit 0 ;;
  "image inspect"*revision*)    echo "${GERCEK_REVISION:-$BEKLENEN_SHA}"; exit 0 ;;
  "image inspect"*)    echo "sha256:eski"; exit 0 ;;
  pull*)               [ "$MOD" = "pull-hata" ] && { echo "manifest unknown" >&2; exit 1; }; exit 0 ;;
  run*pg_dump*)        head -c 4096 /dev/zero | tr '\0' 'X'; exit 0 ;;
  run*pg_restore*)     exit 0 ;;
  run*--entrypoint\ stat*|run*entrypoint*stat*) echo "10001:10001"; exit 0 ;;
  compose*config*)     printf 'SUNGUR_DATA_DIR: /opt/sungur-data\nsource: sungur_data\nread_only: true\n'; exit 0 ;;
  compose*)            exit 0 ;;
  *)                   exit 0 ;;
esac
STUB
  chmod +x "$bin/docker"
}

akis_kos() {
  local mod="$1" kok="$2" bin="$3" cikti="$7" etiket_arg="${8:-$SHA_GECERLI}"
  ( cd "$kok" \
    && PATH="$bin:$PATH" MOD="$mod" BEKLENEN_SHA="$SHA_GECERLI" \
       GERCEK_DIGEST="${4:-sha256:aaaa}" GERCEK_REVISION="${5:-$SHA_GECERLI}" \
       SUNGUR_KOK="$kok" SUNGUR_PROJE=harman-zamani HOME="$kok" \
       BEKLENEN_DIGEST="${6:-}" \
       bash deploy/sunucu-deploy.sh "$etiket_arg" ) >"$cikti" 2>&1
}

AKIS_BIN="$CALISMA/akis-bin"; akis_stub_yaz "$AKIS_BIN"

# Her senaryo HEM dosyanın değişmediğini HEM DE durmanın DOĞRU KAPIDA olduğunu
# doğrular. Yalnız "durdu + dosya aynı" demek yetmez: bir kapı kaldırıldığında
# akış başka bir kapıda düşer, dosya yine değişmez ve test yanlış sebeple yeşil
# kalırdı. Nitekim etiket kapısının çağrısı silindiğinde tam bu oldu.
akis_senaryo() {
  local ad="$1" mod="$2" bek_digest="$3" ger_digest="$4" ger_rev="$5" desen="$6" etiket_arg="${7:-$SHA_GECERLI}"
  local kok="$CALISMA/akis-$ad" cikti="$CALISMA/akis-$ad.log"
  akis_koku_hazirla "$kok"
  local once sonra rc=0
  once="$(sha256sum "$kok/.env.production" | cut -d' ' -f1)"
  akis_kos "$mod" "$kok" "$AKIS_BIN" "$ger_digest" "$ger_rev" "$bek_digest" "$cikti" "$etiket_arg" || rc=$?
  sonra="$(sha256sum "$kok/.env.production" | cut -d' ' -f1)"
  if [ "$rc" -eq 0 ]; then
    kirmizi "I/$ad akış BAŞARILI döndü, oysa durmalıydı"
  elif [ "$once" != "$sonra" ]; then
    kirmizi "I/$ad akış durdu ama .env.production DEĞİŞTİ (doğrulanmamış etiket dosyada kaldı)"
    grep '^APP_IMAGE_TAG=' "$kok/.env.production" | sed 's/^/      /'
  elif ! grep -qF "$desen" "$cikti"; then
    kirmizi "I/$ad YANLIŞ KAPIDA durdu — beklenen mesaj yok: '$desen'"
    tail -3 "$cikti" | sed 's/^/      /'
  else
    yesil "I/$ad doğru kapıda durdu, .env.production byte-for-byte değişmedi (exit $rc)"
  fi
}

akis_senaryo "pull-hata"      "pull-hata" ""                   "sha256:aaaa" "$SHA_GECERLI" "manifest unknown"
akis_senaryo "digest-uyusmaz" "iyi"       "sha256:BEKLENMEYEN" "sha256:aaaa" "$SHA_GECERLI" "digest'i beklenenle eşleşmiyor"
akis_senaryo "surum-uyusmaz"  "iyi"       ""                   "sha256:aaaa" "0000000000000000000000000000000000000000" "aynı commit'ten değil"
akis_senaryo "etiket-kapisi"  "iyi"       ""                   "sha256:aaaa" "$SHA_GECERLI" "40 haneli commit SHA" "develop"

baslik "J) Deploy hijyeni — çalıştırılabilir betikler ve geri dönüş runbook'u"

REPO_KOKU="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BEKLENEN_DEPLOY_SH=5
DEPLOY_INDEXI=""
if ! DEPLOY_INDEXI="$(git -C "$REPO_KOKU" ls-files -s -- deploy 2>/dev/null)"; then
  kirmizi "J1 git indexi okunamadı; deploy betik modları ölçülemedi"
elif ! DEPLOY_SH_SAYISI="$(printf '%s\n' "$DEPLOY_INDEXI" | awk '$4 ~ /^deploy\/.*\.sh$/ {sayi++} END {print sayi+0}')"; then
  kirmizi "J1 deploy betik sayısı ölçülemedi"
elif [ "$DEPLOY_SH_SAYISI" -lt "$BEKLENEN_DEPLOY_SH" ]; then
  kirmizi "J1 deploy betik modları eksik ölçüldü: en az $BEKLENEN_DEPLOY_SH bekleniyordu, $DEPLOY_SH_SAYISI bulundu"
else
  MOD_HATALARI="$(printf '%s\n' "$DEPLOY_INDEXI" | awk '$4 ~ /^deploy\/.*\.sh$/ && $1 != "100755" {print $1 " " $4}')"
  if [ -z "$MOD_HATALARI" ]; then
    yesil "J1 deploy altındaki $DEPLOY_SH_SAYISI takipli .sh dosyasının tümü git modunda 100755"
  else
    kirmizi "J1 çalıştırılabilir olmayan deploy betiği var: $MOD_HATALARI"
  fi
fi

RUNBOOK="$REPO_KOKU/docs/ops/DEPLOY.md"
if grep -qF 'docker rm -f harman-zamani-db-1' "$RUNBOOK" \
   && grep -qF 'docker volume rm harman-zamani_postgres_data' "$RUNBOOK"; then
  yesil "J2 geri dönüş adımı yetim yerel db konteynerini ve volume'unu kaldırıyor"
else
  kirmizi "J2 runbook yetim yerel db temizliğini eksik tarif ediyor"
fi

if grep -qF 'Korunan eski sürüm ağacındaki `deploy/sunucu-deploy.sh` kesinlikle çalıştırılmamalıdır.' "$RUNBOOK" \
   && grep -qF 'boş yerel veritabanını geçerli yedek sanabilir' "$RUNBOOK"; then
  yesil "J3 eski sürüm ağacındaki deploy betiği gerekçesiyle açıkça yasak"
else
  kirmizi "J3 eski deploy betiği yasağı veya sahte yedek gerekçesi eksik"
fi

# J4/J5: sürüm aktarımı prosedürü ve onu bulduran işaret.
#
# Gerekçe: 2026-08-16 deploy'unda dizin takası gerekti ve prosedür bu kurulum
# için yazılı değildi; DEPLOY.md ayrıntı için öbür kurulumun geçiş belgesindeki
# aktarım bölümüne yönlendiriyordu — orası BAŞKA bir kurulumu (sungur-app /
# erp.example.com) anlatıyor ve `--build` ile ÜRETİM KUTUSUNDA derliyor,
# yani 2026-08-05 kesintisini yeniden üretirdi. İki kapı da bunun sessizce geri
# gelmesini engeller: J4 prosedürün güvenliği taşıyan adımlarını, J5 yanlış
# işaretin deploy araçlarına geri konmasını sabitler.
#
# J4 yalnız GÜVENLİĞİ TAŞIYAN komutları sabitler, her cümleyi değil: düzyazı
# düzenlemesinde kırmızı olan bir test, başlığı ilk yeniden yazan kişi
# tarafından silinir.

J4_ADIMLARI=(
  "takas öncesi SHA doğrulaması|sudo grep -qx '<SHA>' /opt/harman-zamani-yeni/deploy/RELEASE_SHA"
  "ağaç eksiksizliği fail-closed|{ echo \"EKSİK: \$f — takas YAPILMAZ\"; exit 1; }"
  ".env yedeği release dizini DIŞINDA|sudo cp -p /opt/harman-zamani/.env.production /home/ubuntu/backups/env.production.yedek-\$TS"
  "takas öncesi bayt karşılaştırması|sudo cmp /opt/harman-zamani/.env.production /opt/harman-zamani-yeni/.env.production"
  "geri alma komutu önceden yazılı|sudo mv /opt/harman-zamani-onceki-<TS> /opt/harman-zamani"
  "geri alma SÜRÜM KİMLİĞİNE bağlı|sudo grep -qx \"\$ONCEKI_SHA\" /opt/harman-zamani/deploy/RELEASE_SHA"
  "kimlik KULLANILABİLİRLİĞİ doğrulanıyor|printf '%s' \"\$ONCEKI_SHA\" | grep -qxE '[0-9a-f]{40}'"
  "kimlik TEK SATIR okunuyor|sudo head -n 1 /opt/harman-zamani-onceki-<TS>/deploy/RELEASE_SHA | tr -d '[:space:]'"
)

for adim in "${J4_ADIMLARI[@]}"; do
  ad="${adim%%|*}"
  desen="${adim#*|}"
  if grep -qF -- "$desen" "$RUNBOOK"; then
    yesil "J4/$ad runbook'ta sabit"
  else
    kirmizi "J4/$ad runbook'tan kayboldu"
  fi
done

# J5: deploy araçları öbür kurulumun geçiş belgesine İŞARET ETMEZ.
#
# 2026-08-16 deploy'unda dizin takası gerekince DEPLOY.md ayrıntı için o
# belgenin aktarım bölümüne yönlendiriyordu; orası sungur-app /
# erp.example.com kurulumunu anlatıyor ve `--build` ile ÜRETİM
# KUTUSUNDA derliyor — yani 2026-08-05 kesintisini yeniden üretirdi. Yanlış
# işaret sessizce geri konabilir; asıl kusur budur.
#
# Yalnız "Sürüm aktarımı" ifadesini aramak yetmez: işaret yeniden
# sözcüklenerek geri gelebilir. Bu yüzden deploy/ ve .github/ altından o
# belgeye HİÇBİR atıf kabul edilmiyor; gereken bilgi DEPLOY.md üzerinden
# verilir.
#
# ÖLÇÜLEN SINIR — burada yazılı, okuyucunun keşfetmesine bırakılmamıştır:
# grep -F tam dizgeyi arar. Küçük harfli yazım, tire ile bölme ve satıra
# bölünmüş hâl bu kapıdan KAÇAR. Tehdit modeli bilinçli gizleme değil,
# alışkanlık ve kopyala-yapıştır ile işaretin geri gelmesidir; kapı tam da
# onu durdurur. Daha geniş bir eşleşme istenirse burası değiştirilir —
# sınırın yazılı olmaması bizi bu PR'a getiren şeydi.
#
# Aranan ad BİLEREK iki parçadan kuruluyor: düz yazılsaydı bu dosya kendi
# kuralını ihlal ederdi ve dosyayı taramadan muaf tutmak, yasağın tam da
# konduğu yerde bir delik bırakırdı.
J5_YASAK_AD="DOMAIN_""MIGRATION"
J5_KAYNAKLAR=""
if ! J5_KAYNAKLAR="$(git -C "$REPO_KOKU" ls-files -- deploy .github 2>/dev/null)"; then
  kirmizi "J5 deploy/.github dosya listesi okunamadı"
else
  J5_IHLAL=""
  for dosya in $J5_KAYNAKLAR; do
    if grep -qF -- "$J5_YASAK_AD" "$REPO_KOKU/$dosya" 2>/dev/null; then
      J5_IHLAL="$J5_IHLAL $dosya"
    fi
  done
  if [ -z "$J5_IHLAL" ]; then
    J5_SAYI="$(printf '%s\n' "$J5_KAYNAKLAR" | grep -c .)"
    yesil "J5 deploy/ ve .github/ altındaki $J5_SAYI dosyanın hiçbiri öbür kurulumun geçiş belgesine işaret etmiyor"
  else
    kirmizi "J5 başka kurulumun prosedürüne işaret eden dosya(lar):$J5_IHLAL"
  fi
fi

# ---------------------------------------------------------------------------
# J6: prosedürdeki HER adım durmaya bağlı olmalı — örnekler değil KURAL.
#
# 4., 5. ve 6. adımlar aynı kusurun üç örneğiydi: doğrulayıp devam eden bir
# kontrol. Üçünü tek tek bağlamak, gelecek ay eklenecek 8. adımı echo ile
# gelmekten alıkoymazdı. Kapı bu yüzden mevcut adımları saymaz; bloktaki
# yapısal olmayan her mantıksal satırda durdurma arar. Bağlanmamış YENİ bir
# adım, hiç tanınmasa bile kırmızıdır.
J6_CIKTI=""
if J6_CIKTI="$(python3 "$REPO_KOKU/deploy/ci-surum-aktarimi-kapisi.py" 2>&1)"; then
  yesil "$J6_CIKTI"
else
  kirmizi "$J6_CIKTI"
fi

# J7: geri alma CANLI DİZİNİ SİLMEZ — örnek değil SINIF sabitlenir.
#
# Geri alma `sudo rm -rf /opt/harman-zamani && sudo mv <onceki> /opt/harman-zamani`
# biçimindeydi. Önce hata denetimi yoktu; `|| { …; exit 1; }` eklendi ve bu
# YETMEDİ: exit 1 hatayı ancak canlı ağaç ZATEN silindikten sonra görünür kılar.
# rm başarılı olup mv başarısız olursa geriye hiçbir şey kalmaz — prosedür, bir
# şeyin ters gittiği anda kurtarmaya çalıştığı nesneyi yok eder.
#
# Örnek onarıldı (geri alma artık yeniden adlandırmayla yapılıyor), ama örneği
# onarıp sınıfı sabitlememek üç turdur kapattığımız kusur ailesinin kendisidir.
# Bu kapı, gelecekte biri yıkıcı sıralamayı geri getirirse kırmızı olur.
J7_CIKTI=""
if J7_CIKTI="$(python3 "$REPO_KOKU/deploy/ci-geri-alma-kapisi.py" 2>&1)"; then
  yesil "$J7_CIKTI"
else
  kirmizi "$J7_CIKTI"
fi

# ---------------------------------------------------------------------------

baslik "K) CI yayın izolasyonu — testler paralel, yayın bütün kapılara bağlı"
# ---------------------------------------------------------------------------
CI_WORKFLOW="$REPO_KOKU/.github/workflows/ci.yml"

ci_is_bloku() {
  local is_adi="$1"
  awk -v hedef="$is_adi" '
    $0 ~ "^  " hedef ":[[:space:]]*$" { iceride=1; print; next }
    iceride && /^  [[:alnum:]_-]+:[[:space:]]*$/ { exit }
    iceride { print }
  ' "$CI_WORKFLOW"
}

YAYIN_ISI=""
CONTAINER_ISI=""
VERIFY_ISI=""
K1_CIKTI=""
if K1_CIKTI="$(python3 "$REPO_KOKU/deploy/ci-yayin-needs-kapisi.py" "$CI_WORKFLOW" 2>&1)"; then
  yesil "$K1_CIKTI"
else
  kirmizi "$K1_CIKTI"
fi

K7_CIKTI=""
if K7_CIKTI="$(python3 "$REPO_KOKU/deploy/ci-postgresql-shard-kapisi.py" "$CI_WORKFLOW" 2>&1)"; then
  yesil "$K7_CIKTI"
else
  kirmizi "$K7_CIKTI"
fi

K8_CIKTI=""
if K8_CIKTI="$(python3 "$REPO_KOKU/deploy/ci-gerekli-baglam-kapisi.py" "$CI_WORKFLOW" 2>&1)"; then
  yesil "$K8_CIKTI"
else
  kirmizi "$K8_CIKTI"
fi

if ! YAYIN_ISI="$(ci_is_bloku publish-image)" \
   || ! CONTAINER_ISI="$(ci_is_bloku container)" \
   || ! VERIFY_ISI="$(ci_is_bloku verify-image-artifact)"; then
  kirmizi "K2 yayın adımının job ayrımı ölçülemedi"
  kirmizi "K3 container bağımlılıkları ölçülemedi"
  kirmizi "K4 test edilmiş imaj artifact zinciri ölçülemedi"
  kirmizi "K5 artifact doğrulama job'ı ölçülemedi"
  kirmizi "K6 attestation politikası ölçülemedi"
else
  if printf '%s\n' "$YAYIN_ISI" \
       | grep -qE '^[[:space:]]*- name: Publish image to GHCR[[:space:]]*$' \
     && printf '%s\n' "$YAYIN_ISI" \
       | grep -qE "^    if: github\\.event_name == 'push' && github\\.ref == 'refs/heads/develop'[[:space:]]*$" \
     && ! printf '%s\n' "$CONTAINER_ISI" \
       | grep -qE '^[[:space:]]*- name: Publish image to GHCR[[:space:]]*$'; then
    yesil "K2 GHCR yayın adımı yalnız publish-image işinde ve job-level develop push kapısında"
  else
    kirmizi "K2 GHCR yayın adımı publish-image dışına taşmış veya job-level develop push kapısı eksik"
  fi

  if [ -n "$CONTAINER_ISI" ] \
     && ! printf '%s\n' "$CONTAINER_ISI" | grep -qE '^    needs:[[:space:]]*'; then
    yesil "K3 container işi upstream bağımlılığı olmadan paralel başlıyor"
  else
    kirmizi "K3 container işinde needs bulundu veya job ölçülemedi"
  fi

  if printf '%s\n' "$CONTAINER_ISI" \
       | grep -qE '^[[:space:]]*- name: Package tested production image[[:space:]]*$' \
     && printf '%s\n' "$CONTAINER_ISI" \
       | grep -qE '^[[:space:]]+docker save yerel-hesap-pro:\$\{\{ github\.sha \}\}[[:space:]]+\\[[:space:]]*$' \
     && printf '%s\n' "$CONTAINER_ISI" \
       | grep -qE '^[[:space:]]*- name: Upload tested production image[[:space:]]*$' \
     && printf '%s\n' "$CONTAINER_ISI" \
       | grep -qE '^[[:space:]]+uses: actions/upload-artifact@v4[[:space:]]*$' \
     && printf '%s\n' "$YAYIN_ISI" \
       | grep -qE '^[[:space:]]*- name: Download tested production image[[:space:]]*$' \
     && printf '%s\n' "$YAYIN_ISI" \
       | grep -qE '^[[:space:]]+uses: actions/download-artifact@v4[[:space:]]*$' \
     && printf '%s\n' "$YAYIN_ISI" \
       | grep -qF '        run: gzip -dc "$RUNNER_TEMP/tested-production-image/tested-production-image.tar.gz" | docker load' \
     && ! printf '%s\n' "$YAYIN_ISI" \
       | grep -qE '^[[:space:]]+(run:[[:space:]]*)?docker[[:space:]]+([^[:space:]]+[[:space:]]+)*build([[:space:]]|$)' \
     && ! printf '%s\n' "$YAYIN_ISI" \
       | grep -qE '^[[:space:]]+uses:[[:space:]]+docker/build-push-action@'; then
    yesil "K4 container'da test edilen imaj artifact ile taşınıyor; publish-image yeniden build etmiyor"
  else
    kirmizi "K4 test edilen imajın save/upload/download/load zinciri eksik veya publish-image yeniden build ediyor"
  fi

  # ---------------------------------------------------------------------
  # K5 — ÇAĞRI YERİ. Ne ölçer, ne ÖLÇMEZ: burada yazılı.
  #
  # Bu, aynı kusurun ÜÇÜNCÜ turudur. #6'da K5 üç SABİT DİZGE arıyordu; dört
  # kaçış yeşil ölçüldü. #7'de K5 akıllandı (karşılaştırmayı İZLEYEN satırda
  # `exit 1`, tek atama, beklentinin kaynağı); dört kaçış daha bulundu ve
  # ÜÇÜ DE o kontrollerden geçti: `image_id="$expected_image_id"` (atama
  # SOLDAKİ ada bakan sayıma görünmez), `if false; then … fi`, alt kabuk +
  # dışarıda `|| true`, ve karşılaştırmadan önce `exit 0`.
  #
  # Her iki tur da metin üzerinden bir AKIŞ DENETİMİ özelliği kanıtlamaya
  # çalıştı. KONUM, BAŞARISIZLIK SEMANTİĞİ İÇİN YETERLİ BİR DEĞİŞMEZ DEĞİLDİR.
  # Bu yüzden kapı mantığı `deploy/artifact-imaj-kimlik-kapisi.sh`e taşındı;
  # onun GERÇEKTEN kırmızı verdiğini K8 ÇALIŞTIRARAK ölçer. K5'e kalan iş,
  # metnin dürüstçe çivileyebileceği tek şeydir: adımın O BETİĞİ çağırması ve
  # çağrının etkisiz bırakılmamış olması.
  #
  # K5'İN GARANTİ ETTİĞİ: `verify-image-artifact` işinin adım listesi TAM
  # OLARAK beklenen dörtlüdür (araya betiği ezen bir adım sokulamaz), çağrı
  # satırı TAM OLARAK `run: ./deploy/artifact-imaj-kimlik-kapisi.sh`tır
  # (`|| true`, alt kabuk, `if false` gibi sarmalamalar bu tam-satır eşleşmesini
  # BOZAR), `shell:` ile `{0}` şablonu üzerinden sarmalama yoktur, beklenti
  # `needs.container.outputs.tested_image_id`den gelir, işin KOŞULSUZ koştuğu
  # (job-level `if:` yok — onunla kapı hiç koşmadan yok edilebilirdi, ÖLÇÜLDÜ)
  # `needs.container.outputs.tested_image_id`den gelir ve `container` o kimliği
  # `docker save`in girdisiyle AYNI nesneden okur.
  #
  # K5'İN GARANTİ ETMEDİĞİ: betiğin İÇİNDE kapı olduğu — bunu K8 ölçer;
  # sözleşme betiğinin KENDİSİNİN düzenlenmesi — K5 de K8 de kendi dosyasını
  # korumaz, bu tur bunu kapatmaz.
  # ---------------------------------------------------------------------
  # Beklenti, `docker save`in girdisiyle AYNI nesneden okunmalı. K4 save
  # hedefini zaten çiviliyor; burada kimliğin O hedeften okunduğu çivilenir.
  # Başka bir nesneden (ör. tarball'ın kendisinden) okunan bir kimlik,
  # kapıyı yine kendi kopyasına baktırırdı.
  K5_KIMLIK_KAYNAGI='          tested_image_id="$(docker image inspect yerel-hesap-pro:${{ github.sha }} --format '"'"'{{.Id}}'"'"')"'
  K5_KAPI_BETIGI="deploy/artifact-imaj-kimlik-kapisi.sh"
  K5_CAGRI_SATIRI="        run: ./$K5_KAPI_BETIGI"

  # Adım listesi TAM eşleşmeli. Yalnız "yasak deseni yok" demek AÇIK UÇLU bir
  # iddiadır ve tam da kaçılmaya çalışılan whack-a-mole'dur; adımları SAYIP
  # SIRALAMAK ise KAPALI bir kümedir: araya sokulan HER adım bunu bozar.
  K5_ADIM_LISTESI="$(printf '%s\n' "$VERIFY_ISI" \
    | sed -n 's/^      - \(name\|uses\): //p' | tr '\n' '|')"
  K5_BEKLENEN_ADIMLAR='actions/checkout@v4|Download tested production image|Load tested production image|Verify loaded image identity and OCI revision|'

  if printf '%s\n' "$VERIFY_ISI" \
       | grep -qE '^    needs:[[:space:]]*\[container\][[:space:]]*$' \
     && [ "$K5_ADIM_LISTESI" = "$K5_BEKLENEN_ADIMLAR" ] \
     && printf '%s\n' "$VERIFY_ISI" \
       | grep -qE '^[[:space:]]+uses: actions/download-artifact@v4[[:space:]]*$' \
     && printf '%s\n' "$VERIFY_ISI" \
       | grep -qF '        run: gzip -dc "$RUNNER_TEMP/tested-production-image/tested-production-image.tar.gz" | docker load' \
     && [ -x "$REPO_KOKU/$K5_KAPI_BETIGI" ] \
     && [ "$(printf '%s\n' "$VERIFY_ISI" | grep -cxF "$K5_CAGRI_SATIRI")" = "1" ] \
     && ! printf '%s\n' "$VERIFY_ISI" | grep -qE '^[[:space:]]+shell:[[:space:]]' \
     && ! printf '%s\n' "$VERIFY_ISI" | grep -qE '^    if:[[:space:]]' \
     && printf '%s\n' "$VERIFY_ISI" \
       | grep -qxF '          IMAJ_REF: yerel-hesap-pro:${{ github.sha }}' \
     && printf '%s\n' "$VERIFY_ISI" \
       | grep -qxF '          BEKLENEN_IMAJ_KIMLIGI: ${{ needs.container.outputs.tested_image_id }}' \
     && printf '%s\n' "$VERIFY_ISI" \
       | grep -qxF '          BEKLENEN_OCI_REVIZYONU: ${{ github.sha }}' \
     && printf '%s\n' "$CONTAINER_ISI" \
       | grep -qF '      tested_image_id: ${{ steps.paketle.outputs.tested_image_id }}' \
     && printf '%s\n' "$CONTAINER_ISI" \
       | grep -qF "$K5_KIMLIK_KAYNAGI" \
     && ! printf '%s\n' "$VERIFY_ISI" | grep -qE '^[[:space:]]+packages:[[:space:]]+write[[:space:]]*$'; then
    yesil "K5 doğrulama adımı kapı betiğini SARMALANMAMIŞ tek satırda çağırıyor, adım listesi tam, beklenti container'ın SAVE ettiği nesneden geliyor ve packages:write yok"
  else
    kirmizi "K5 doğrulama adımı kapı betiğini tek satırda çağırmıyor (sarmalanmış veya shell: ile şablonlanmış olabilir), adım listesi beklenenden farklı (araya adım sokulmuş), betik yok/çalıştırılabilir değil, beklenti container çıktısına bağlı değil veya packages:write var"
  fi

  K6_BUILD_SAYISI=""
  K6_POLITIKA_SAYISI=""
  if ! K6_BUILD_SAYISI="$(printf '%s\n' "$CONTAINER_ISI" \
       | awk '/^[[:space:]]+(run:[[:space:]]*)?docker[[:space:]]+([^[:space:]]+[[:space:]]+)*build([[:space:]]|$)/ { sayi++ } END { print sayi+0 }')" \
     || ! K6_POLITIKA_SAYISI="$(printf '%s\n' "$CONTAINER_ISI" \
       | awk '/^[[:space:]]+docker build --provenance=false --sbom=false --target production[[:space:]]+\\[[:space:]]*$/ { sayi++ } END { print sayi+0 }')"; then
    kirmizi "K6 container build komutları ölçülemedi"
  elif [ "$K6_BUILD_SAYISI" -eq 1 ] \
     && [ "$K6_POLITIKA_SAYISI" -eq 1 ] \
     && ! printf '%s\n' "$CONTAINER_ISI" \
       | grep -qE '^[[:space:]]+uses:[[:space:]]+docker/build-push-action@'; then
    yesil "K6 local save/load yayınında provenance ve SBOM bilinçli olarak kapalı"
  else
    kirmizi "K6 build sayısı/politikası hatalı (build=$K6_BUILD_SAYISI, bayraklı=$K6_POLITIKA_SAYISI) veya registry-direct yol karışmış"
  fi
fi


baslik "K8) Artifact kimlik kapısı — DAVRANIŞSAL: kapı gerçekten kırmızı veriyor mu"

# ---------------------------------------------------------------------------
# NEDEN BURASI METİN DEĞİL DAVRANIŞ ÖLÇÜYOR
#
# K5 iki turdur metin üzerinden bir AKIŞ DENETİMİ özelliği kanıtlamaya çalıştı
# ve iki turda da yeni kaçış şekilleri çıktı: `|| true`, `exit 1` yerine `echo`,
# ikinci bir atama, `|| [ 1 = 1 ]`, `image_id="$expected_image_id"`,
# `if false; then … fi`, alt kabuk + dışarıda `|| true`, erken `exit 0`.
# Hepsi karşılaştırmanın KIRMIZIYA BAĞLANIŞINI bozar. Bir metin kontrolü bu
# kümeyi kapatamaz, çünkü küme AÇIK UÇLUDUR.
#
# Bu yüzden buradaki kapı, CI adımının çağırdığı BETİĞİN TA KENDİSİNİ çalıştırır
# ve kirli girdide SIFIRDAN FARKLI çıkmasını ŞART KOŞAR. Yukarıdaki kaçışların
# hepsi "kirli girdide yine de exit 0" demektir; hepsi burada kırmızıdır.
#
# TUZAK — bu depo bunu bir kez yaşadı: beş yeşil frontend testi kendi mock'una
# iddia ederken iki gerçek gösterim yolu da bozuktu. Bu yüzden test kapının bir
# KOPYASINI değil, `ci.yml`in çağırdığı AYNI dosyayı çalıştırır; K5 de çağrılan
# yolun bu dosya olduğunu çiviler. İkisi birlikte TEK artefakta bakar.
#
# OLUMLU DURUM DA ÖLÇÜLÜR: her zaman kırmızı veren bir betik de "kirli girdide
# kırmızı" şartını sağlar ve kapıyı kullanışsız kılardı. Doğru beklentide YEŞİL
# şartı o boş geçişi kapatır.
#
# DOCKER YOKSA KIRMIZI, ATLAMA YOK: bu betik CI'da `container` işinde koşar ve
# orada Docker HER ZAMAN vardır. Atlanan bir kapı, geçen bir kapı değildir.
# ---------------------------------------------------------------------------
K8_KAPI="$REPO_KOKU/deploy/artifact-imaj-kimlik-kapisi.sh"
K8_LOG="$(mktemp)"
K8_YAPI="$(mktemp -d)"

k8_kos() {
  # CI adımının yaptığının AYNISI: depo kökünden, GÖRELİ yolla, aynı ortam
  # değişkenleriyle. Farklı bir çağrı biçimi (ör. mutlak yol) mutantın
  # "$0"a bakıp üretimi ayırt etmesine kapı açardı.
  (
    cd "$REPO_KOKU" \
      && IMAJ_REF="$1" BEKLENEN_IMAJ_KIMLIGI="$2" BEKLENEN_OCI_REVIZYONU="$3" \
         ./deploy/artifact-imaj-kimlik-kapisi.sh
  ) >"$K8_LOG" 2>&1 && printf '0\n' || printf '%s\n' "$?"
}

k8_olc() {
  ad="$1"; kirmizi_olmali="$2"; shift 2
  rc="$(k8_kos "$@")"
  if [ "$kirmizi_olmali" = "1" ]; then
    if [ "$rc" != "0" ]; then
      yesil "K8/$ad kapı KIRMIZI verdi (exit $rc) — etkisizleştirme bu koşulda yaşayamaz"
    else
      kirmizi "K8/$ad kapı EXIT 0 verdi; kirli girdide yeşil kalan bir kapı, kapı DEĞİLDİR"
    fi
  else
    if [ "$rc" = "0" ]; then
      yesil "K8/$ad kapı doğru girdide YEŞİL — her zaman kırmızı veren boş bir kapı değil"
    else
      kirmizi "K8/$ad kapı doğru girdide KIRMIZI verdi (exit $rc); kapı kullanılamaz: $(head -c 200 "$K8_LOG")"
    fi
  fi
}

if [ ! -x "$K8_KAPI" ]; then
  kirmizi "K8 kapı betiği yok veya çalıştırılabilir değil: deploy/artifact-imaj-kimlik-kapisi.sh"
elif ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  kirmizi "K8 Docker daemon'ına ulaşılamadı; kapı DAVRANIŞSAL olarak ölçülemedi (atlama geçme değildir)"
else
  # GERÇEK bir imaj kurulur. `FROM scratch` ağ istemez; ölçüm kayıt sunucusuna
  # bağlı değildir.
  # Fikstür, ÜRETİM girdisinden AYIRT EDİLEMEZ olmalı: aynı imaj adı ve aynı
  # 40 haneli SHA biçimi. Ayırt edilebilir bir fikstür (ör. başka bir imaj adı)
  # ölçüldü ve KAÇIŞ ÜRETTİ: `case "$IMAJ_REF" in yerel-hesap-pro:*) exit 0` diyen
  # bir mutant K8'i geçip CI'da kapıyı öldürüyordu. Fikstür üretime benzedikçe
  # "teste göre davran" numarası zorlaşır.
  K8_REV="0123456789abcdef0123456789abcdef01234567"
  K8_ETIKET="yerel-hesap-pro:$K8_REV"
  printf 'FROM scratch\nLABEL org.opencontainers.image.revision=%s\n' "$K8_REV" > "$K8_YAPI/Dockerfile"
  K8_GERCEK_ID=""
  if ! docker build -q -t "$K8_ETIKET" "$K8_YAPI" >"$K8_LOG" 2>&1; then
    kirmizi "K8 ölçüm imajı kurulamadı: $(head -c 200 "$K8_LOG")"
  elif ! K8_GERCEK_ID="$(docker image inspect "$K8_ETIKET" --format '{{.Id}}')" || [ -z "$K8_GERCEK_ID" ]; then
    kirmizi "K8 ölçüm imajının kimliği okunamadı"
  else
    # "Yanlış" beklenti UYDURMA bir değer değil, BAŞKA GERÇEK bir imajın
    # kimliğidir: bu, CI'daki takas senaryosunun ta kendisidir. Uydurma bir
    # nöbetçi değer (ör. sırf sıfırlardan oluşan bir ID) kapının İÇİNDE
    # tanınabilirdi ve "yalnız teste göre kırmızı ver" diyen bir mutant K8'i
    # geçerdi. Gerçek bir ikinci imaj bu numarayı kapatır.
    printf 'FROM scratch\nLABEL org.opencontainers.image.revision=%s\nLABEL takas=1\n' "$K8_REV" > "$K8_YAPI/Dockerfile"
    K8_BASKA_ID=""
    if docker build -q -t "$K8_ETIKET-baska" "$K8_YAPI" >"$K8_LOG" 2>&1; then
      K8_BASKA_ID="$(docker image inspect "$K8_ETIKET-baska" --format '{{.Id}}')"
    fi
    if [ -z "$K8_BASKA_ID" ] || [ "$K8_BASKA_ID" = "$K8_GERCEK_ID" ]; then
      kirmizi "K8 ikinci (takas) ölçüm imajı kurulamadı veya birinciyle aynı; takas senaryosu ölçülemedi"
    else
      k8_olc "olumlu-dogru-beklenti"    0 "$K8_ETIKET" "$K8_GERCEK_ID" "$K8_REV"
      k8_olc "kimlik-uyusmazligi"       1 "$K8_ETIKET" "$K8_BASKA_ID" "$K8_REV"
      k8_olc "bos-beklenti-fail-closed" 1 "$K8_ETIKET" "" "$K8_REV"
      k8_olc "revizyon-uyusmazligi"     1 "$K8_ETIKET" "$K8_GERCEK_ID" "ffffffffffffffffffffffffffffffffffffffff"
    fi
  fi
  docker image rm -f "$K8_ETIKET" >/dev/null 2>&1 || true
  docker image rm -f "$K8_ETIKET-baska" >/dev/null 2>&1 || true
fi
rm -rf "$K8_YAPI" "$K8_LOG"

printf '\n%s\n' "SONUÇ: $GECTI geçti, $KALDI kaldı"
[ "$KALDI" -eq 0 ] || exit 1
