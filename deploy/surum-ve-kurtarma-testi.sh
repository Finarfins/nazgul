#!/usr/bin/env bash
# deploy/sunucu-deploy.sh içindeki fail-closed kapıların testi:
#   A) sürüm sözleşmesi   — surum_sozlesmesi_dogrula()
#   B) kurtarma doğrulama — kurtarma_dogrula()
#   C) volume tohumlama   — hacmi_tohumla()
#   D) kapanış sonrası yarış — son snapshot ile yeniden doğrulama
#   E) hata sonrası güvenli devam
#
# Betik, deploy betiğini SUNGUR_DEPLOY_KAYNAK=evet ile source eder; böylece
# gerçek fonksiyonlar test edilir, kopyaları değil.
#
# TASARIM KURALI: her beklenen-hata testi HEM sıfırdan farklı çıkışı HEM DE
# kendine özgü hata mesajını doğrular. Yalnız çıkış koduna bakmak yetmez —
# fonksiyon doğrulamaya hiç varmadan çökerse (ör. set -u altında unbound
# parametre) bu da "sıfırdan farklı" döner ve test sahte biçimde geçer.
# Bu tam olarak bir kez yaşandı: kurtarma_dogrula() üçüncü parametreyi
# zorunlu okuyordu, iki argümanla çağrılınca ölüyordu ve B1-B4 sahte geçiyordu.
#
# CI: .github/workflows/ci.yml → `container` işi (gerçek üretim imajıyla).
# Yerelde: IMAJ=<imaj> ./deploy/surum-ve-kurtarma-testi.sh
set -uo pipefail

IMAJ="${IMAJ:?IMAJ verilmeli (ör. yerel-hesap-pro:<sha>)}"
KOK_DIZIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CALISMA="$(mktemp -d)"
ONEK="svkt-$$"
KAP="$ONEK-kaynak"
HACIM="${ONEK}_sungur_data"
GECTI=0; KALDI=0

yesil()   { printf '  \033[32m✓ %s\033[0m\n' "$*"; GECTI=$((GECTI+1)); }
kirmizi() { printf '  \033[31m✗ %s\033[0m\n' "$*"; KALDI=$((KALDI+1)); }
baslik()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

# beklenen_hata <etiket> <cikti> <beklenen_desen> -- <komut...>
# Sıfırdan farklı çıkış VE desenin eşleşmesi birlikte aranır.
beklenen_hata() {
  local etiket="$1" cikti="$2" desen="$3"; shift 3
  [ "${1:-}" = "--" ] && shift
  local rc=0
  ( set +e; "$@" >"$cikti" 2>&1 ); rc=$?
  if [ "$rc" -eq 0 ]; then
    kirmizi "$etiket — GEÇTİ, oysa durmalıydı"
  elif ! grep -q "$desen" "$cikti"; then
    kirmizi "$etiket — durdu ama beklenen mesaj yok: '$desen'"
    echo "      gelen çıktı: $(head -c 200 "$cikti" | tr '\n' ' ')"
  else
    yesil "$etiket (exit $rc, mesaj doğrulandı)"
  fi
}

beklenen_gecis() {
  local etiket="$1" cikti="$2"; shift 2
  local rc=0
  ( set +e; "$@" >"$cikti" 2>&1 ); rc=$?
  if [ "$rc" -eq 0 ]; then yesil "$etiket"
  else
    kirmizi "$etiket — exit $rc, oysa geçmeliydi"
    echo "      çıktı: $(head -c 300 "$cikti" | tr '\n' ' ')"
  fi
}

esit_mi() {
  local etiket="$1" beklenen="$2" gelen="$3"
  if [ -z "$beklenen" ]; then
    kirmizi "$etiket — beklenen değer boş (geçersiz karşılaştırma)"
    return 1
  fi
  if [ -z "$gelen" ]; then
    kirmizi "$etiket — gelen değer boş (geçersiz karşılaştırma)"
    return 1
  fi
  if [ "$beklenen" = "$gelen" ]; then yesil "$etiket ($beklenen)"
  else kirmizi "$etiket — beklenen [$beklenen], gelen [$gelen]"; fi
}

# guvenli_al <etiket> <hedef_degisken> -- <komut...>
#
# STDOUT/STDERR AYRIMI (zorunlu): yakalanan değer YALNIZ stdout'tur. stderr ayrı
# bir dosyaya gider ve sadece hata teşhisinde ekrana basılır — hiçbir koşulda
# `$hedef`e, dolayısıyla guvenli_hash'in hash girdisine karışmaz. Birleştirilmiş
# yakalama (`2>&1`) boş bir manifesti "dolu" gösteriyordu: üreticinin stderr'e
# bastığı tek satırlık bir docker uyarısı, sıfır dosyalık bir manifesti boşluk
# kapısından geçiriyor ve aynı uyarı iki çağrıda üretildiğinde hash eşitliğiyle
# sahte yeşil doğuruyordu (D7).
#
# stderr KARAR KOŞULU DEĞİLDİR. Boşluk ve geçerlilik yalnız stdout üzerinden
# belirlenir; stderr sadece hata mesajının teşhis satırında görünür. "stderr
# üretti → kırmızı" kuralı Y4'ü kapatıyordu ama bedeli ağırdı: docker'ın olağan
# uyarıları (platform mismatch, "Unable to find image locally") sağlıklı bir
# manifesti sahte kırmızıya çeviriyor ve C3/C4/D4/D7/E4/E5'i ortama bağımlı hâle
# getiriyordu. Y4 zaten stdout tarafında kapalı: stderr değere karışmadığı için
# sıfır dosyalık bir manifest boş kalır, boşluk kapısı ısırır ve hash üretilmez.
#
# EXIT CODE SÖZLEŞMESİ: tek ve açık kaynak, alt kabuğun çıkış kodudur.
# `PIPESTATUS` alt kabukta üreticiden HEMEN SONRA, araya tek bir komut bile
# girmeden yakalanır — `rc=$?` dahil herhangi bir komut PIPESTATUS'u sıfırlar.
# Ardından alt kabuk üreticinin kendi koduyla `exit` eder, böylece `rc` gerçekten
# o kodu taşır. Önceki sürümde alt kabuğun son komutu PIPESTATUS'u yazan `printf`
# olduğu için `rc` her zaman 0 kalıyor ve kontrol ölü dala dönüşüyordu.
guvenli_al() {
  local etiket="$1" hedef="$2"
  shift 2
  local cikti cikti_dosyasi durum_dosyasi hata_dosyasi rc durumlar durum hata_ozeti
  cikti_dosyasi="$(mktemp)"
  hata_dosyasi="$(mktemp)"
  durum_dosyasi="$(mktemp)"

  (
    set -o pipefail
    "$@" >"$cikti_dosyasi" 2>"$hata_dosyasi"
    _ps=("${PIPESTATUS[@]}")
    printf '%s\n' "${_ps[*]}" > "$durum_dosyasi"
    exit "${_ps[0]}"
  )
  rc=$?
  durumlar="$(cat "$durum_dosyasi")"
  rm -f "$durum_dosyasi"

  # Yalnız teşhis; hiçbir karar koşulunda kullanılmaz.
  hata_ozeti="$(head -c 200 "$hata_dosyasi" | tr '\n' ' ')"

  if [ "$rc" -ne 0 ]; then
    kirmizi "$etiket — üreten komut hata ile bitti (exit $rc, pipeline: ${durumlar:-bilinmiyor})"
    [ -n "$hata_ozeti" ] && echo "      stderr: $hata_ozeti"
    rm -f "$cikti_dosyasi" "$hata_dosyasi"
    return 1
  fi
  if [ -z "$durumlar" ]; then
    kirmizi "$etiket — pipeline durumu yakalanamadı (pipeline: bilinmiyor)"
    [ -n "$hata_ozeti" ] && echo "      stderr: $hata_ozeti"
    rm -f "$cikti_dosyasi" "$hata_dosyasi"
    return 1
  fi
  for durum in $durumlar; do
    if [ "$durum" -ne 0 ]; then
      kirmizi "$etiket — pipeline aşaması başarısız (pipeline: $durumlar)"
      [ -n "$hata_ozeti" ] && echo "      stderr: $hata_ozeti"
      rm -f "$cikti_dosyasi" "$hata_dosyasi"
      return 1
    fi
  done

  cikti="$(cat "$cikti_dosyasi")"
  rm -f "$cikti_dosyasi" "$hata_dosyasi"
  if [ -z "$cikti" ]; then
    kirmizi "$etiket — üreten komut boş stdout verdi (stderr karar koşulu değildir)"
    [ -n "$hata_ozeti" ] && echo "      stderr: $hata_ozeti"
    return 1
  fi
  printf -v "$hedef" '%s' "$cikti"
}

guvenli_hash() {
  local etiket="$1" hedef_hash="$2" hedef_raw="$3"
  shift 3
  local ham

  if ! guvenli_al "${etiket} için ham manifest" "$hedef_raw" "$@"; then
    return 1
  fi

  ham="${!hedef_raw}"
  if [ -z "$ham" ]; then
    kirmizi "$etiket — ham manifest boş"
    return 1
  fi
  printf -v "$hedef_hash" '%s' "$(printf '%s' "$ham" | sha256sum | cut -d' ' -f1)"
}

dosya_say() {
  if [ -d "$1" ]; then
    find "$1" -type f -name "$2" | wc -l | tr -d ' '
  else
    grep -Fc -- "$2" "$1"
  fi
}

# --- D1/D2 assertion'ları: TEK tanım -----------------------------------------
# Hem normal akış hem NP4/NP5 bu fonksiyonları çağırır. Probe'un assertion
# mantığının bir KOPYASINI içermesi, ölçtüğünü sandığı regresyonu ölçmemesi
# demekti: D1/D2 tekrar koşulsuz `yesil`e çevrilse bile kendi kopyasını sınayan
# probe yeşil kalıyordu. Tek tanım sayesinde böyle bir geri adım NP4/NP5'i
# zorunlu olarak kırar.
d1_kopyayi_al() {
  docker cp "$KAP:/app/backend/data" "$1" >/dev/null
}

d1_operator_kopyasi() {
  if d1_kopyayi_al "$1"; then
    yesil "D1 operatör kopyası alındı (uygulama açıkken)"
    return 0
  else
    kirmizi "D1 operatör kopyası alınamadı"
    return 1
  fi
}

d2_yaris_yaz() {
  docker exec -u 0 "$KAP" sh -c \
    "printf 'yaris-penceresinde-yazildi' > /app/backend/data/attachments/1/7/yaris.jpg"
}

d2_yaris_dosyasini_yaz() {
  if d2_yaris_yaz; then
    yesil "D2 kopyadan SONRA yeni attachment yazıldı (yarış penceresi)"
    return 0
  else
    kirmizi "D2 kopyadan SONRA attachment yazılamadı"
    return 1
  fi
}

# np_mutasyon <etiket> <beklenen_kirmizi_mesaj> -- <üretim_assertion_fonksiyonu> [arg...]
# D1/D2 assertion'ını `docker` stub'ı 77 dönerken gerçek kod yolunu çağırıp,
# üretilen kırmızı mesajı doğrular. Alt kabukta koşar: stub, sayaçlar ve çıktı
# orada kalır, ana GEÇTİ/KALDI kirlenmez ve harness sonraki senaryoya devam eder.
np_mutasyon() {
  local etiket="$1"
  local beklenen_mesaj="$2"
  shift 2
  [ "${1:-}" = "--" ] && shift
  local onceki_gecti="$GECTI" onceki_kaldi="$KALDI"
  local cikti_dosyasi hata_dosyasi sayac_dosyasi sayac
  local ic_gecti ic_kaldi cmd_rc
  cikti_dosyasi="$(mktemp)"
  hata_dosyasi="$(mktemp)"
  sayac_dosyasi="$(mktemp)"

  # Mutasyonun gerçekten bir başarısızlık ürettiğini ALT KABUĞUN SAYAÇLARINDAN
  # okuruz, komutun çıkış kodundan değil. Gerekçe: probe bilerek ÜRETİM
  # assertion'ını çağırıyor (O6) ve bir assertion her zaman 0 döner — `yesil` de
  # `kirmizi` de aritmetik atamayla biter. "Komut non-zero dönmeli" şartı bu
  # yüzden assertion yoluyla bağdaşmaz; onu koymak NP4/NP5'i koşulsuz kırmızıya
  # çevirir. Doğru sinyal: assertion tam olarak bir KIRMIZI, hiç yeşil üretmeli.
  (
    docker() { return 77; }
    GECTI=0; KALDI=0
    "$@" >"$cikti_dosyasi" 2>"$hata_dosyasi"
    _rc=$?
    printf '%s %s %s\n' "$GECTI" "$KALDI" "$_rc" > "$sayac_dosyasi"
  )
  read -r ic_gecti ic_kaldi cmd_rc < "$sayac_dosyasi" || true
  rm -f "$sayac_dosyasi"
  ic_gecti="${ic_gecti:-bilinmiyor}"; ic_kaldi="${ic_kaldi:-bilinmiyor}"; cmd_rc="${cmd_rc:-bilinmiyor}"

  if [ "$onceki_gecti" -ne "$GECTI" ] || [ "$onceki_kaldi" -ne "$KALDI" ]; then
    kirmizi "$etiket — sayaç kirlenmesi (başlangıç $onceki_gecti/$onceki_kaldi, şimdi $GECTI/$KALDI)"
    rm -f "$cikti_dosyasi" "$hata_dosyasi"
    return
  fi
  if [ -s "$hata_dosyasi" ]; then
    kirmizi "$etiket — assertion stderr üretti (komut çıkışı: $cmd_rc)"
    rm -f "$cikti_dosyasi" "$hata_dosyasi"
    return
  fi
  if [ "$ic_gecti" != "0" ] || [ "$ic_kaldi" != "1" ]; then
    kirmizi "$etiket — mutasyon beklenen tek kırmızıyı üretmedi (geçti/kaldı: $ic_gecti/$ic_kaldi, komut çıkışı: $cmd_rc)"
    [ -s "$cikti_dosyasi" ] && cat "$cikti_dosyasi"
    rm -f "$cikti_dosyasi" "$hata_dosyasi"
    return
  fi
  if [ -z "$beklenen_mesaj" ]; then
    kirmizi "$etiket — beklenen kırmızı mesaj verilmedi"
    rm -f "$cikti_dosyasi" "$hata_dosyasi"
    return
  fi
  if grep -qF "$beklenen_mesaj" "$cikti_dosyasi"; then
    yesil "$etiket"
  else
    kirmizi "$etiket — kırmızı üretildi ama beklenen mesaj yok: '$beklenen_mesaj'"
    [ -s "$cikti_dosyasi" ] && cat "$cikti_dosyasi"
  fi
  rm -f "$cikti_dosyasi" "$hata_dosyasi"
}

temizle() {
  docker rm -f "$KAP" >/dev/null 2>&1
  docker volume rm -f "$HACIM" >/dev/null 2>&1
  docker rmi -f "$ONEK/etiketsiz:1" >/dev/null 2>&1
  git -C "$KOK_DIZIN" checkout -- deploy/RELEASE_SHA 2>/dev/null \
    || printf '$Format:%%H$\n' > "$KOK_DIZIN/deploy/RELEASE_SHA"
  # Deploy betiği source edildiği için koşuya özel geçici dizini onun kendi
  # temizleyicisiyle kaldırırız. Sabit /tmp yolları artık kullanılmıyor; betik
  # her koşuda kendi mktemp dizinini açıyor (sunucu-deploy.sh::_gecici_alan_hazirla).
  # Deploy betiği EXIT trap'ini yalnız doğrudan çalıştırıldığında kurar, bu
  # yüzden burada elle çağırmak gerekir — aksi hâlde harness'in trap'i ezilirdi.
  command -v gecici_alan_temizle >/dev/null 2>&1 && gecici_alan_temizle
  rm -rf "$CALISMA"
}
trap temizle EXIT

cd "$KOK_DIZIN"

export SUNGUR_DEPLOY_KAYNAK=evet
export SUNGUR_PROJE="$ONEK" SUNGUR_APP_KABI="$KAP" SUNGUR_HACIM="$HACIM" SUNGUR_KOK="$KOK_DIZIN"
# shellcheck source=/dev/null
. ./deploy/sunucu-deploy.sh
unset SUNGUR_DEPLOY_KAYNAK
# Deploy betiği `set -euo pipefail` ile başlar (üretimde öyle KALMALI) ve source
# edilince bunu çağıran kabuğa da uygular. Bu test negatif dönüşler üzerine
# kurulu; -e açık kalırsa ilk beklenen hata testi öldürür.
set +e
_gecici_alan_hazirla

# ---------------------------------------------------------------------------
baslik "A) Sürüm sözleşmesi — fail-closed"
# ---------------------------------------------------------------------------
GERCEK_SHA="$(docker image inspect "$IMAJ" \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null | tr -d '[:space:]')"
docker build -q -t "$ONEK/etiketsiz:1" - >/dev/null <<EOF
FROM $IMAJ
LABEL org.opencontainers.image.revision=
EOF
cp .env.production.example "$CALISMA/env"
cat >> "$CALISMA/env" <<'EOF'
POSTGRES_PASSWORD=test-only-password-yeterince-uzun
TURNSTILE_SECRET_KEY=test-dummy
BOOTSTRAP_ADMIN_PASSWORD=test-dummy-bootstrap-pw
EOF
DC=(docker compose -p "$ONEK" -f docker-compose.yml -f docker-compose.prod.yml --env-file "$CALISMA/env")

# A1 — eksik disk SHA: RELEASE_SHA yer tutucu VE git deposu yok.
a1_calistir() {
  cd "$CALISMA/gitsiz" || return 9
  SUNGUR_DEPLOY_KAYNAK=evet . ./deploy/sunucu-deploy.sh || return 9
  set +e
  DC=(docker compose -p "$ONEK" -f docker-compose.yml -f docker-compose.prod.yml --env-file "$CALISMA/env")
  surum_sozlesmesi_dogrula "$IMAJ"
}
mkdir -p "$CALISMA/gitsiz"
cp -r deploy docker-compose.yml docker-compose.prod.yml .env.production.example "$CALISMA/gitsiz/"
printf '$Format:%%H$\n' > "$CALISMA/gitsiz/deploy/RELEASE_SHA"
beklenen_hata "A1 eksik disk SHA (git yok, yer tutucu) → durdu" "$CALISMA/a1" \
  "Diskteki sürümün SHA'sı belirlenemedi" -- a1_calistir
cd "$KOK_DIZIN"

# A2 — eksik image revision etiketi.
printf '%s\n' "$GERCEK_SHA" > deploy/RELEASE_SHA
beklenen_hata "A2 eksik OCI revision etiketi → durdu" "$CALISMA/a2" \
  "revision etiketi yok" -- surum_sozlesmesi_dogrula "$ONEK/etiketsiz:1"

# A3 — farklı SHA.
printf '%s\n' "0000000000000000000000000000000000000000" > deploy/RELEASE_SHA
beklenen_hata "A3 farklı SHA → durdu" "$CALISMA/a3" \
  "aynı commit'ten değil" -- surum_sozlesmesi_dogrula "$IMAJ"

# A4 — kaçış yolu kalmamalı. YALNIZ ÇALIŞAN KOD sayılır; yorumda anılması
# serbest. Aksi hâlde "bu kaçış bir kez eklendi ve kaldırıldı, tekrarlamayın"
# diyen bir yorum kapıyı kırardı — nitekim kırdı. Bir kapının, kendi
# gerekçesini yazan yoruma takılması onu daha sıkı değil, daha gürültülü yapar.
# `file:satır:` öneki soyulup satırın kendisi yorum mu diye bakılır (C4 ile aynı desen).
A4_KOD="$(grep -n 'SURUM_SHA_ATLA' deploy/sunucu-deploy.sh 2>/dev/null \
          | sed -E 's/^[0-9]+://' | grep -vE '^[[:space:]]*#' || true)"
if [ -n "$A4_KOD" ]; then
  kirmizi "A4 SURUM_SHA_ATLA kaçışı hâlâ çalışan kodda"
  printf '      %s\n' "$A4_KOD"
else
  yesil "A4 SURUM_SHA_ATLA kaçışı çalışan kodda yok (yalnız yorumda anılıyor)"
fi

# A5 — üretim betiği `set -e` ile korunmalı (testi geçirmek için düşürülmemeli).
if grep -qE '^set -euo pipefail' deploy/sunucu-deploy.sh; then
  yesil "A5 üretim betiği set -euo pipefail ile korunuyor"
else
  kirmizi "A5 üretim betiğinde set -e yok — hata sessizce yutulabilir"
fi

# A6 — eşleşen SHA geçmeli.
printf '%s\n' "$GERCEK_SHA" > deploy/RELEASE_SHA
beklenen_gecis "A6 eşleşen SHA → geçti" "$CALISMA/a6" surum_sozlesmesi_dogrula "$IMAJ"
git checkout -- deploy/RELEASE_SHA 2>/dev/null || printf '$Format:%%H$\n' > deploy/RELEASE_SHA

# ---------------------------------------------------------------------------
baslik "B) Kurtarma doğrulaması — fail-closed"
# ---------------------------------------------------------------------------
docker run -d --name "$KAP" --entrypoint sleep "$IMAJ" 3600 >/dev/null
docker exec -u 0 "$KAP" sh -c "
  mkdir -p /app/backend/data/attachments/1/7 /app/backend/data/backups
  printf 'imza-verisi-1' > /app/backend/data/attachments/1/7/a.jpg
  printf 'imza-verisi-2' > /app/backend/data/attachments/1/7/b.png
  printf 'dump-govdesi'  > /app/backend/data/backups/backup-x.dump"
yesil "kaynak konteyner 3 dosyayla hazırlandı"

beklenen_hata "B1 kurtarma dizini yok → durdu" "$CALISMA/b1" \
  "KURTARMA_DIZINI, 'docker cp' çıktısını içeren dizin olmalı" -- \
  kurtarma_dogrula "$KAP" "$CALISMA/olmayan"

mkdir -p "$CALISMA/iyi"
docker cp "$KAP:/app/backend/data" "$CALISMA/iyi" >/dev/null

cp -r "$CALISMA/iyi" "$CALISMA/eksik"; rm -f "$CALISMA/eksik/data/attachments/1/7/b.png"
beklenen_hata "B2 kopyada dosya eksik → durdu" "$CALISMA/b2" \
  "kurtarma kopyası kaynakla EŞLEŞMİYOR" -- \
  kurtarma_dogrula "$KAP" "$CALISMA/eksik"
if grep -qF "b.png" "$CALISMA/b2" && \
   grep -qF "kurtarma kopyası kaynakla EŞLEŞMİYOR" "$CALISMA/b2"; then
  yesil "B2 fark kanıtı: b.png eksikliği görüldü"
else
  kirmizi "B2 fark kanıtı: b.png veya beklenen mesaj bulunamadı"
fi

cp -r "$CALISMA/iyi" "$CALISMA/bozuk"; printf 'BOZULMUS' > "$CALISMA/bozuk/data/attachments/1/7/a.jpg"
beklenen_hata "B3 kopyada içerik bozuk → durdu" "$CALISMA/b3" \
  "kurtarma kopyası kaynakla EŞLEŞMİYOR" -- \
  kurtarma_dogrula "$KAP" "$CALISMA/bozuk"
if grep -qF "a.jpg" "$CALISMA/b3" && \
   grep -qF "kurtarma kopyası kaynakla EŞLEŞMİYOR" "$CALISMA/b3"; then
  yesil "B3 fark kanıtı: a.jpg bozukluğu görüldü"
else
  kirmizi "B3 fark kanıtı: a.jpg veya beklenen mesaj bulunamadı"
fi

mkdir -p "$CALISMA/bos/data"
beklenen_hata "B4 kopya boş → durdu" "$CALISMA/b4" \
  "kurtarma kopyası kaynakla EŞLEŞMİYOR" -- \
  kurtarma_dogrula "$KAP" "$CALISMA/bos"

beklenen_gecis "B5 doğru kopya → manifest eşleşti" "$CALISMA/b5" \
  kurtarma_dogrula "$KAP" "$CALISMA/iyi" "" "$KURTARMA_MANIFEST"

cp "$KURTARMA_MANIFEST" "$CALISMA/yabanci-manifest.txt"
beklenen_hata "B6 farklı koşunun manifest yolu → durdu" "$CALISMA/b6" \
  "manifesti bu koşunun geçici alanına ait değil" -- \
  hacmi_tohumla "$CALISMA/iyi" "$IMAJ" "$CALISMA/yabanci-manifest.txt"

# ---------------------------------------------------------------------------
baslik "C) Volume tohumlama — çakışma ve doğrulama"
# ---------------------------------------------------------------------------
docker volume create "$HACIM" >/dev/null
docker run --rm -u 0:0 -v "$HACIM:/h" "$IMAJ" \
  sh -c 'mkdir -p /h/attachments/1/7 && printf "BASKA-BIR-DOSYA" > /h/attachments/1/7/a.jpg' >/dev/null
beklenen_hata "C1 hedefte dosya çakışması → durdu (sessizce atlamadı)" "$CALISMA/c1" \
  "çakışma: ./attachments/1/7/a.jpg" -- hacmi_tohumla "$CALISMA/iyi" "$IMAJ" "$KURTARMA_MANIFEST"

docker volume rm -f "$HACIM" >/dev/null
beklenen_gecis "C2 temiz hedefe tohumlama → manifest doğrulandı" "$CALISMA/c2" \
  hacmi_tohumla "$CALISMA/iyi" "$IMAJ" "$KURTARMA_MANIFEST"

if guvenli_hash "C3 için hacim manifest hash'i" C3_HACIM C3_HACIM_RAW hacim_manifesti "$HACIM" "$IMAJ"; then
  if guvenli_hash "C3 için kaynak manifest hash'i" C3_KAYNAK C3_KAYNAK_RAW konteyner_manifesti "$KAP" /app/backend/data; then
    esit_mi "C3 volume hash'leri kaynakla birebir aynı" "$C3_HACIM" "$C3_KAYNAK"
  fi
fi
if guvenli_al "C4 tohumlanan volume sahipliği" C4_SAHIP docker run --rm -u 0:0 -v "$HACIM:/h" "$IMAJ" stat -c '%u:%g' /h; then
  esit_mi "C4 tohumlanan volume sahipliği" "10001:10001" "$C4_SAHIP"
fi
[ -d "$CALISMA/iyi/data" ] && yesil "C5 kurtarma dizini korundu" || kirmizi "C5 kurtarma dizini silinmiş"

# ---------------------------------------------------------------------------
baslik "D) Kapanış sonrası yarış — son snapshot farkı yakalanmalı"
# ---------------------------------------------------------------------------
# Senaryo: operatör kopyayı alır; UYGULAMA HÂLÂ AÇIKKEN yeni bir attachment
# yazılır; uygulama durdurulur. Kapanıştan sonra alınan snapshot operatörün
# kopyasından farklıdır ve deploy DURMALIDIR — yoksa yeni dosya sessizce kaybolur.
mkdir -p "$CALISMA/ilk-kopya"
d1_operator_kopyasi "$CALISMA/ilk-kopya"
d2_yaris_dosyasini_yaz

docker stop "$KAP" >/dev/null
esit_mi "D3 uygulama durduruldu" "$(docker inspect -f '{{.State.Running}}' "$KAP")" "false"

# Durmuş konteynerden docker cp ile son snapshot (docker exec burada çalışmaz).
mkdir -p "$CALISMA/son-snapshot"
kaynak_snapshot_al "$KAP" "$CALISMA/son-snapshot" >/dev/null 2>&1
if guvenli_al "D4 snapshot'ta yaris.jpg sayısı" D4_YARIS dosya_say "$CALISMA/son-snapshot/data" "yaris.jpg"; then
  esit_mi "D4 durmuş konteynerden snapshot alındı" "$D4_YARIS" "1"
fi

HACIM_ONCE=""
if ! guvenli_hash "D7 öncesi hacim hash'i" HACIM_ONCE HACIM_ONCE_RAW hacim_manifesti "$HACIM" "$IMAJ"; then
  :
fi
beklenen_hata "D5 son doğrulama farkı yakaladı → deploy durdu" "$CALISMA/d5" \
  "kurtarma kopyası kaynakla EŞLEŞMİYOR" -- \
  kurtarma_dogrula "$KAP" "$CALISMA/ilk-kopya" "$CALISMA/son-snapshot" "$KURTARMA_MANIFEST"
grep -q "yaris.jpg" "$CALISMA/d5" && yesil "D6 farkta yeni dosya adı gösterildi" \
  || kirmizi "D6 fark çıktısında yaris.jpg görünmedi"
if guvenli_hash "D7 fark sonrası hacim hash'i" D7_HACIM D7_HACIM_RAW hacim_manifesti "$HACIM" "$IMAJ"; then
  esit_mi "D7 fark hâlinde volume'a DOKUNULMADI" "$D7_HACIM" "$HACIM_ONCE"
fi

# ---------------------------------------------------------------------------
baslik "E) Hata sonrası güvenli devam"
# ---------------------------------------------------------------------------
# Akış, son doğrulama başarısız olursa eski uygulamayı yeniden başlatır
# (deploy/sunucu-deploy.sh, 6/7 adımı) — operatör kapalı bir siteyle kalmamalı.
docker start "$KAP" >/dev/null 2>&1
esit_mi "E1 eski uygulama yeniden başlatılabildi" \
  "$(docker inspect -f '{{.State.Running}}' "$KAP")" "true"

# Güncel snapshot ile tekrar denendiğinde geçmeli ve volume tohumlanmalı.
beklenen_gecis "E2 güncel snapshot ile yeniden doğrulama geçti" "$CALISMA/e2" \
  kurtarma_dogrula "$KAP" "$CALISMA/son-snapshot" "$CALISMA/son-snapshot" "$KURTARMA_MANIFEST"
docker volume rm -f "$HACIM" >/dev/null
beklenen_gecis "E3 güncel kopya ile volume tohumlandı" "$CALISMA/e3" \
  hacmi_tohumla "$CALISMA/son-snapshot" "$IMAJ" "$KURTARMA_MANIFEST"
if guvenli_hash "E4 için hacim hash'i" E4_HACIM E4_HACIM_RAW hacim_manifesti "$HACIM" "$IMAJ"; then
  if guvenli_hash "E4 için snapshot hash'i" E4_SNAPSHOT E4_SNAPSHOT_RAW dizin_manifesti "$CALISMA/son-snapshot/data"; then
    esit_mi "E4 volume hash'i güncel snapshot ile eşit" "$E4_HACIM" "$E4_SNAPSHOT"
  fi
fi
if guvenli_al "E5 için volume manifest" E5_MANIFEST hacim_manifesti "$HACIM" "$IMAJ"; then
  if echo "$E5_MANIFEST" | grep -qF "yaris.jpg"; then
    yesil "E5 yarış dosyası volume'a taşındı"
  else
    kirmizi "E5 yarış dosyası volume manifestte bulunamadı"
  fi
fi
[ -d "$CALISMA/ilk-kopya/data" ] && yesil "E6 eski kurtarma dizini silinmedi" \
  || kirmizi "E6 eski kurtarma dizini silinmiş"

# Negatif probe: eski `esit_mi` ile `$() == $()` boş olduğunda sahte geçiş riskinin
# sayaçları kirletmeden izolasyonu.
NP1_GECTI="$GECTI"
NP1_KALDI="$KALDI"
NP1_SONUC=$(
  (esit_mi "NP1: iki başarısız üretim komutu boş sonuç verince karşılaştırma asla geçmemeli" \
    "$(sh -c 'exit 3')" "$(sh -c 'exit 4')" >/dev/null; [ $? -eq 0 ] && echo PASS || echo FAIL)
)
if [ "$NP1_GECTI" -ne "$GECTI" ] || [ "$NP1_KALDI" -ne "$KALDI" ]; then
  kirmizi "NP1: sayaç kirlenmesi — başlangıç $NP1_GECTI/$NP1_KALDI, şimdi $GECTI/$KALDI"
elif [ "$NP1_SONUC" = "PASS" ]; then
  kirmizi "NP1: eski boş==boş sahte geçiş tespit edildi"
else
  yesil "NP1: eski boş==boş sahte geçiş izole edildi"
fi

# Negatif probe: üretici pipeline'ın erken aşaması düşerse ve son aşama başarılı olsa da fail.
NP2_GECTI="$GECTI"
NP2_KALDI="$KALDI"
NP2_SONUC=$(
  (guvenli_al "NP2: pipeline erken hatası yakalanmalı" NP2_PIPE bash -c 'set -o pipefail; false | printf "ok"'; [ $? -eq 0 ] && echo PASS || echo FAIL)
)
if [ "$NP2_GECTI" -ne "$GECTI" ] || [ "$NP2_KALDI" -ne "$KALDI" ]; then
  kirmizi "NP2: sayaç kirlenmesi — başlangıç $NP2_GECTI/$NP2_KALDI, şimdi $GECTI/$KALDI"
elif [ "$NP2_SONUC" = "PASS" ]; then
  kirmizi "NP2: erken pipeline başarısızlığı maskelenerek geçti"
else
  yesil "NP2: erken pipeline başarısızlığı doğru yakalandı"
fi

# Negatif probe: ham manifest boş ise hash karşılaştırması kabul etmemeli.
NP3_GECTI="$GECTI"
NP3_KALDI="$KALDI"
NP3_SONUC=$(
  (guvenli_hash "NP3: boş manifest hash karşılaştırması" NP3_HASH NP3_HASH_RAW sh -c 'printf ""'; [ $? -eq 0 ] && echo PASS || echo FAIL)
)
if [ "$NP3_GECTI" -ne "$GECTI" ] || [ "$NP3_KALDI" -ne "$KALDI" ]; then
  kirmizi "NP3: sayaç kirlenmesi — başlangıç $NP3_GECTI/$NP3_KALDI, şimdi $GECTI/$KALDI"
elif [ "$NP3_SONUC" = "PASS" ]; then
  kirmizi "NP3: boş manifest karşılaştırması maskelenerek geçti"
else
  yesil "NP3: boş manifest karşılaştırması doğru reddedildi"
fi

# Negatif probe: docker cp 77 dönerken ÜRETİM D1 assertion'ı kırmızı kalmalı.
np_mutasyon "NP4: docker cp non-zero durumunda D1 kırmızı kalır" \
  "D1 operatör kopyası alınamadı" -- \
  d1_operator_kopyasi "$CALISMA/np4-hedef"

# Negatif probe: docker exec 77 dönerken ÜRETİM D2 assertion'ı kırmızı kalmalı.
np_mutasyon "NP5: docker exec non-zero durumunda D2 kırmızı kalır" \
  "D2 kopyadan SONRA attachment yazılamadı" -- \
  d2_yaris_dosyasini_yaz

# Negatif probe (Y4 regresyon kilidi): üretici stdout'u TAMAMEN boş, stderr'e tek
# satır uyarı basıyor ve exit 0 dönüyor. Birleştirilmiş yakalamada bu "dolu"
# görünüp boşluk kapısından geçiyor, aynı uyarı iki çağrıda üretildiğinde de
# hash eşitliğiyle sahte yeşil doğuruyordu. Ayrıca iki ayrı çağrının hash
# üretmediği — yani karşılaştırmaya hiç varılmadığı — doğrulanır.
NP6_GECTI="$GECTI"
NP6_KALDI="$KALDI"
NP6_SONUC=$(
  bos_stdout_uyarili() { echo "WARNING: platform mismatch" >&2; return 0; }
  guvenli_hash "NP6/1" NP6_H1 NP6_R1 bos_stdout_uyarili >/dev/null 2>&1
  birinci=$?
  guvenli_hash "NP6/2" NP6_H2 NP6_R2 bos_stdout_uyarili >/dev/null 2>&1
  ikinci=$?
  if [ "$birinci" -ne 0 ] && [ "$ikinci" -ne 0 ] && [ -z "${NP6_H1:-}" ] && [ -z "${NP6_H2:-}" ]; then
    echo FAIL   # doğru davranış: ikisi de reddedildi, hash hiç üretilmedi
  else
    echo PASS   # sahte geçiş: stderr değere karışmış
  fi
)
if [ "$NP6_GECTI" -ne "$GECTI" ] || [ "$NP6_KALDI" -ne "$KALDI" ]; then
  kirmizi "NP6: sayaç kirlenmesi — başlangıç $NP6_GECTI/$NP6_KALDI, şimdi $GECTI/$KALDI"
elif [ "$NP6_SONUC" = "PASS" ]; then
  kirmizi "NP6: stderr gürültüsü boş manifesti dolu gösterdi (sahte yeşil)"
else
  yesil "NP6: boş stdout + stderr uyarısı doğru reddedildi (stderr hash'e karışmıyor)"
fi

printf '\n%s\n' "SONUÇ: $GECTI geçti, $KALDI kaldı"
[ "$KALDI" -eq 0 ] || exit 1
