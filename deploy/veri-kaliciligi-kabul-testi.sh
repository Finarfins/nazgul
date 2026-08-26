#!/usr/bin/env bash
# Kalıcı veri hacmi (sungur_data) kabul testi — GERÇEK uygulama imajıyla.
#
# NEREDE ÇALIŞIR
#   1. CI  : .github/workflows/ci.yml → `container` işi, imaj derlendikten hemen
#            sonra. Her PR'da otomatik koşar; PR #27'nin kabul koşulu budur.
#   2. Sunucu: canlı yığına DOKUNMADAN, ayrı bir kaptan ve tek kullanımlık bir
#            volume ile aynı testi koşar (varsayılan mod izoledir).
#
# İKİ MOD
#   (varsayılan)         Tam yaşam döngüsü. Kendi postgres'ini ve kendi
#                        tek kullanımlık volume'unu yaratır, sonunda siler.
#                        Üretim volume'una ve üretim veritabanına DOKUNMAZ.
#   --yalnizca-dogrula   Çalışan üretim yığınına karşı SALT OKUMA doğrulama.
#                        Yazma yok, restart yok, silme yok. Kurtarma sonrası
#                        "gerçekten yerinde mi" kontrolü için.
#
# KULLANIM
#   ./deploy/veri-kaliciligi-kabul-testi.sh                       # :develop imajı
#   IMAJ=ghcr.io/finarfins/nazgul:<sha> ./deploy/...       # belirli sürüm
#   ./deploy/veri-kaliciligi-kabul-testi.sh --yalnizca-dogrula
set -euo pipefail

IMAJ="${IMAJ:-ghcr.io/finarfins/nazgul:develop}"
ONEK="${ONEK:-vkt-$$}"
AG="$ONEK-net"
DB_KAP="$ONEK-db"
APP_KAP="$ONEK-app"
HACIM="$ONEK-vol"
DB_PAROLA="kabul-testi-parolasi"
ADMIN_PW_ILK="admin123"
ADMIN_PW="KabulTesti!12345"
GECTI=0
KALDI=0

kirmizi() { printf '  \033[31m✗ %s\033[0m\n' "$*"; KALDI=$((KALDI + 1)); }
yesil()   { printf '  \033[32m✓ %s\033[0m\n' "$*"; GECTI=$((GECTI + 1)); }
baslik()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
onayla()  { if [ "$2" = "$3" ]; then yesil "$1 ($2)"; else kirmizi "$1 — beklenen [$3], gelen [$2]"; fi; }

komut_var() { command -v "$1" >/dev/null 2>&1; }
for k in docker curl python3; do
  komut_var "$k" || { echo "HATA: '$k' gerekli ama bulunamadı." >&2; exit 2; }
done

# JSON alanı okuyucu. jq her sunucuda yok; python3 var.
jsonal() { python3 -c 'import json,sys
d=json.load(sys.stdin)
for p in sys.argv[1].split("."):
    d = d[int(p)] if p.isdigit() else d[p]
print(d)' "$1"; }

temizle() {
  [ "${TEMIZLIK_KAPALI:-}" = "evet" ] && return 0
  docker rm -f "$APP_KAP" "$DB_KAP" >/dev/null 2>&1 || true
  docker volume rm -f "$HACIM" >/dev/null 2>&1 || true
  docker network rm "$AG" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# SALT OKUMA MOD — çalışan üretim yığını
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--yalnizca-dogrula" ]; then
  KAP="${UYGULAMA_KABI:-harman-zamani-app-1}"
  baslik "Üretim yığını doğrulaması (salt okuma) — kap: $KAP"
  docker inspect "$KAP" >/dev/null 2>&1 || { echo "HATA: $KAP çalışmıyor." >&2; exit 1; }

  onayla "kök read-only" \
    "$(docker inspect "$KAP" --format '{{.HostConfig.ReadonlyRootfs}}' | tr -d '[:space:]')" "true"
  onayla "SUNGUR_DATA_DIR" \
    "$(docker exec "$KAP" sh -c 'echo -n "$SUNGUR_DATA_DIR"')" "/opt/sungur-data"
  onayla "veri kökü sahipliği" \
    "$(docker exec "$KAP" stat -c '%u:%g' /opt/sungur-data)" "10001:10001"
  onayla "/opt/sungur-data bir mount noktası" \
    "$(docker inspect "$KAP" --format '{{range .Mounts}}{{if eq .Destination "/opt/sungur-data"}}{{.Type}}{{end}}{{end}}')" "volume"
  onayla "uygulama non-root" \
    "$(docker exec "$KAP" id -u | tr -d '[:space:]')" "10001"

  EK_SAYISI="$(docker exec "$KAP" sh -c 'find /opt/sungur-data/attachments -type f 2>/dev/null | wc -l' | tr -d '[:space:]')"
  YEDEK_SAYISI="$(docker exec "$KAP" sh -c 'find /opt/sungur-data/backups -name "*.dump" 2>/dev/null | wc -l' | tr -d '[:space:]')"
  echo "  bilgi: $EK_SAYISI attachment dosyası, $YEDEK_SAYISI yedek volume'da"

  printf '\n%s\n' "SONUÇ: $GECTI geçti, $KALDI kaldı"
  [ "$KALDI" -eq 0 ] || exit 1
  exit 0
fi

# ---------------------------------------------------------------------------
# TAM YAŞAM DÖNGÜSÜ — izole
# ---------------------------------------------------------------------------
trap temizle EXIT
baslik "Kalıcı veri hacmi kabul testi — imaj: $IMAJ"

# Üretim bayraklarının birebir aynısı: read-only kök, cap-drop, non-root,
# no-new-privileges, /tmp tmpfs ve sungur_data volume'u.
uygulamayi_baslat() {
  docker run -d --name "$APP_KAP" --network "$AG" --init \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL --security-opt no-new-privileges \
    -v "$HACIM:/opt/sungur-data" \
    -e SUNGUR_DATA_DIR=/opt/sungur-data \
    -e SUNGUR_PLATFORM_OPERATORS=1 \
    -e "DATABASE_URL=postgresql+psycopg://kabul:$DB_PAROLA@$DB_KAP:5432/kabul" \
    -e AUTO_MIGRATE=true -e COOKIE_SECURE=false \
    -p 15050:5050 "$IMAJ" >/dev/null
}

hazir_bekle() {
  for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:15050/api/ready >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "HATA: /api/ready 120 sn içinde yanıt vermedi" >&2
  docker logs "$APP_KAP" --tail 40 >&2
  return 1
}

# Attachment ve yedek dosyalarının sha256'sı — kalıcılık kanıtının ölçüsü.
# Kapsam bilerek bu iki ağaçla sınırlı: `.operation.*` kilit/durum dosyaları her
# çalıştırmada değişir, `restore_journal.jsonl` ise append-only bir denetim
# kaydıdır — ikisi de meşru biçimde değişir ve karşılaştırmayı gürültüye boğardı.
# `xargs -r`: eşleşme yoksa sha256sum argümansız çalışıp stdin'i beklemesin.
hacim_ozeti() {
  docker run --rm -u 0:0 -v "$HACIM:/d:ro" "$IMAJ" \
    sh -c 'cd /d && find ./attachments ./backups -type f 2>/dev/null | sort | xargs -r sha256sum 2>/dev/null'
}

baslik "0) İmaj ve temiz başlangıç sözleşmesi"
if docker run --rm --entrypoint sh "$IMAJ" \
  -c 'test -d /app && test -z "$(find /app -type f -name restore_journal.jsonl -print -quit)"'; then
  yesil "imaj katmanında restore_journal.jsonl yok"
else
  kirmizi "imaj katmanında restore_journal.jsonl bulundu"
fi

baslik "1) Altyapı"
temizle
docker network create "$AG" >/dev/null
docker volume create "$HACIM" >/dev/null
if docker run --rm --entrypoint sh -v "$HACIM:/d:ro" "$IMAJ" \
  -c 'test ! -e /d/restore_journal.jsonl'; then
  yesil "boş kalıcı hacim journalsız"
else
  kirmizi "boş kalıcı hacimde beklenmeyen journal var"
fi
docker run -d --name "$DB_KAP" --network "$AG" \
  -e POSTGRES_DB=kabul -e POSTGRES_USER=kabul -e POSTGRES_PASSWORD="$DB_PAROLA" \
  postgres:16-alpine >/dev/null
for _ in $(seq 1 40); do
  docker exec "$DB_KAP" pg_isready -U kabul -d kabul >/dev/null 2>&1 && break
  sleep 2
done
yesil "postgres hazır"
uygulamayi_baslat
hazir_bekle
yesil "uygulama /api/ready 200"
if docker exec "$APP_KAP" test ! -e /opt/sungur-data/restore_journal.jsonl; then
  yesil "journal olmadan temiz başlangıç"
else
  kirmizi "temiz başlangıç journal üretti"
fi

baslik "2) Volume ve sertleştirme sözleşmesi"
onayla "veri kökü sahipliği (copy-up)" \
  "$(docker exec "$APP_KAP" stat -c '%u:%g' /opt/sungur-data)" "10001:10001"
onayla "uygulama non-root" "$(docker exec "$APP_KAP" id -u | tr -d '[:space:]')" "10001"
if docker exec "$APP_KAP" sh -c 'touch /app/backend/data/sizinti' >/dev/null 2>&1; then
  kirmizi "kök read-only DEĞİL — /app/backend/data yazılabildi"
else
  yesil "kök read-only (yazma reddedildi)"
fi

baslik "3) Runtime journal kalıcı veri dizini sözleşmesi"
onayla "runtime SUNGUR_DATA_DIR" \
  "$(docker exec "$APP_KAP" sh -c 'printf %s "$SUNGUR_DATA_DIR"')" "/opt/sungur-data"
docker exec -w /app/backend "$APP_KAP" python -c \
  'from app.config import settings; from app.restore_journal import append_restore_journal; append_restore_journal(settings.sungur_data_dir, "acceptance.runtime_journal", operation_id="acceptance-runtime-journal", owner="acceptance-test", details={"scope": "persistent-data"})'
JOURNAL_YOLLARI="$(docker exec "$APP_KAP" sh -c \
  'find /app /opt/sungur-data -type f -name restore_journal.jsonl -print 2>/dev/null | sort')"
onayla "runtime journal yalnız kalıcı veri dizininde" \
  "$JOURNAL_YOLLARI" "/opt/sungur-data/restore_journal.jsonl"
onayla "runtime journal kalıcı hacimden okunuyor" \
  "$(docker run --rm --entrypoint sh -v "$HACIM:/d:ro" "$IMAJ" -c \
    'grep -c '"'"'"event": "acceptance.runtime_journal"'"'"' /d/restore_journal.jsonl')" "1"

baslik "4) Oturum"
GIRIS="$(curl -fsS -X POST http://127.0.0.1:15050/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW_ILK\"}")"
TOKEN="$(printf '%s' "$GIRIS" | jsonal access_token)"
SIRKET="$(printf '%s' "$GIRIS" | jsonal companies.0.id)"
KULLANICI="$(printf '%s' "$GIRIS" | jsonal user.id)"
onayla "bootstrap admin kullanıcı id (platform operatörü)" "$KULLANICI" "1"
DEGIS="$(curl -fsS -X POST http://127.0.0.1:15050/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" -H "X-Company-ID: $SIRKET" \
  -H 'Content-Type: application/json' \
  -d "{\"current_password\":\"$ADMIN_PW_ILK\",\"new_password\":\"$ADMIN_PW\"}")"
TOKEN="$(printf '%s' "$DEGIS" | jsonal access_token)"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "X-Company-ID: $SIRKET")
yesil "oturum açıldı (şirket $SIRKET)"

baslik "5) Attachment upload → download → hash"
MUSTERI="$(curl -fsS -X POST http://127.0.0.1:15050/api/customers "${AUTH[@]}" \
  -H 'Content-Type: application/json' -d '{"name":"Kabul Testi Müşterisi"}' | jsonal id)"
MAKINE="$(curl -fsS -X POST http://127.0.0.1:15050/api/machines "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"customer_id\":$MUSTERI,\"brand\":\"Sungur\",\"model\":\"KT-1\",\"serial_number\":\"KT-SER-1\",\"chassis_number\":\"KT-CHS-1\"}" | jsonal id)"
IS_EMRI="$(curl -fsS -X POST http://127.0.0.1:15050/api/work-orders "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"machine_id\":$MAKINE,\"technician_id\":$KULLANICI,\"complaint\":\"Kalıcılık testi\",\"actual_hours\":\"1\",\"labor_rate\":\"100\"}" | jsonal id)"
yesil "iş emri $IS_EMRI oluşturuldu"

CALISMA="$(mktemp -d)"
head -c 4096 /dev/urandom > "$CALISMA/foto.jpg"
KAYNAK_HASH="$(sha256sum "$CALISMA/foto.jpg" | cut -d' ' -f1)"
EK="$(curl -fsS -X POST "http://127.0.0.1:15050/api/work-order-attachments/$IS_EMRI" "${AUTH[@]}" \
  -F "file=@$CALISMA/foto.jpg;type=image/jpeg" -F 'kind=photo')"
EK_ID="$(printf '%s' "$EK" | jsonal id)"
onayla "upload boyutu" "$(printf '%s' "$EK" | jsonal file_size)" "4096"
curl -fsS "http://127.0.0.1:15050/api/work-order-attachments/$IS_EMRI/$EK_ID/download" "${AUTH[@]}" \
  -o "$CALISMA/indirilen.jpg"
onayla "download hash = upload hash" "$(sha256sum "$CALISMA/indirilen.jpg" | cut -d' ' -f1)" "$KAYNAK_HASH"
onayla "dosya volume'da" \
  "$(docker exec "$APP_KAP" sh -c "find /opt/sungur-data/attachments/$SIRKET/$IS_EMRI -type f | wc -l" | tr -d '[:space:]')" "1"

baslik "6) Platform yedeği"
# Durum kodunu ve gövdeyi ayrı yakalıyoruz: bu uç konteynerin İÇİNDEKİ pg_dump'ı
# çağırır, dolayısıyla istemci/sunucu sürüm uyuşmazlığı gibi bir arıza burada
# çıplak bir curl hatası değil, okunabilir bir mesaj olarak görünmeli.
YEDEK_HTTP="$(curl -s -o /tmp/vkt-yedek.json -w '%{http_code}' \
  -X POST http://127.0.0.1:15050/api/platform/backups "${AUTH[@]}" -d '')"
if [ "$YEDEK_HTTP" != "201" ]; then
  kirmizi "platform yedeği HTTP $YEDEK_HTTP (beklenen 201)"
  echo "  yanıt : $(head -c 500 /tmp/vkt-yedek.json)"
  echo "  imajdaki pg_dump: $(docker exec "$APP_KAP" pg_dump --version 2>&1 | head -1)"
  echo "  sunucu sürümü   : $(docker exec "$DB_KAP" postgres --version 2>&1 | head -1)"
  echo "  uygulama logu   :"; docker logs "$APP_KAP" --tail 20 2>&1 | sed 's/^/    /'
  printf '\n%s\n' "SONUÇ: $GECTI geçti, $KALDI kaldı"
  exit 1
fi
YEDEK_ADI="$(jsonal name < /tmp/vkt-yedek.json)"
yesil "yedek oluşturuldu: $YEDEK_ADI"
for uzanti in "" ".sha256" ".manifest.json"; do
  if docker exec "$APP_KAP" test -f "/opt/sungur-data/backups/$YEDEK_ADI$uzanti"; then
    yesil "volume'da: $YEDEK_ADI$uzanti"
  else
    kirmizi "volume'da YOK: $YEDEK_ADI$uzanti"
  fi
done

ONCE="$(hacim_ozeti)"
DOSYA_SAYISI="$(printf '%s\n' "$ONCE" | grep -c . || true)"
echo "  bilgi: volume'da $DOSYA_SAYISI dosya izleniyor"

baslik "7) restart sonrası kalıcılık"
docker restart "$APP_KAP" >/dev/null
hazir_bekle
onayla "restart: dosya hash'leri" "$(hacim_ozeti | sha256sum | cut -d' ' -f1)" "$(printf '%s\n' "$ONCE" | sha256sum | cut -d' ' -f1)"
onayla "restart: indirme hâlâ 200" \
  "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:15050/api/work-order-attachments/$IS_EMRI/$EK_ID/download" "${AUTH[@]}")" "200"

baslik "8) force-recreate sonrası kalıcılık (kap yok edilir, volume kalır)"
docker rm -f "$APP_KAP" >/dev/null
uygulamayi_baslat
hazir_bekle
onayla "force-recreate: dosya hash'leri" "$(hacim_ozeti | sha256sum | cut -d' ' -f1)" "$(printf '%s\n' "$ONCE" | sha256sum | cut -d' ' -f1)"
onayla "force-recreate: veri kökü sahipliği" \
  "$(docker exec "$APP_KAP" stat -c '%u:%g' /opt/sungur-data)" "10001:10001"
GIRIS2="$(curl -fsS -X POST http://127.0.0.1:15050/api/auth/login \
  -H 'Content-Type: application/json' -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW\"}")"
AUTH=(-H "Authorization: Bearer $(printf '%s' "$GIRIS2" | jsonal access_token)" -H "X-Company-ID: $SIRKET")
onayla "force-recreate: indirme hâlâ 200" \
  "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:15050/api/work-order-attachments/$IS_EMRI/$EK_ID/download" "${AUTH[@]}")" "200"

baslik "9) Dolu volume ile tekrar deploy (idempotens)"
for tekrar in 1 2; do
  docker rm -f "$APP_KAP" >/dev/null
  uygulamayi_baslat
  hazir_bekle
  onayla "tekrar $tekrar: hash'ler sabit" "$(hacim_ozeti | sha256sum | cut -d' ' -f1)" "$(printf '%s\n' "$ONCE" | sha256sum | cut -d' ' -f1)"
  onayla "tekrar $tekrar: sahiplik sabit" \
    "$(docker exec "$APP_KAP" stat -c '%u:%g' /opt/sungur-data)" "10001:10001"
done

baslik "10) Yeni yazma hâlâ çalışıyor (volume salt okunur hâle gelmedi)"
EK2="$(curl -fsS -X POST "http://127.0.0.1:15050/api/work-order-attachments/$IS_EMRI" "${AUTH[@]}" \
  -F "file=@$CALISMA/foto.jpg;type=image/jpeg" -F 'kind=signature')"
onayla "ikinci upload" "$(printf '%s' "$EK2" | jsonal kind)" "signature"

rm -rf "$CALISMA"
printf '\n%s\n' "SONUÇ: $GECTI geçti, $KALDI kaldı"
[ "$KALDI" -eq 0 ] || exit 1
