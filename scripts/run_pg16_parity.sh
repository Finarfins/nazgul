#!/usr/bin/env bash
# Gerçek PostgreSQL 16 parity koşumu.
#
# Her test dosyası KENDİ TAZE veritabanına karşı koşar. Bu şart: dosyalar tek
# veritabanını paylaşır ve erkenci bir dosya admin parolasını döndürerek sonraki
# dosyanın login'ini `KeyError: 'companies'` ile düşürür. CI bunu her matrix
# girdisine temiz bir konteyner vererek çözer; bu script aynı izolasyonu
# veritabanını dosya başına yeniden yaratarak sağlar.
#
# Kullanım:
#   bash scripts/run_pg16_parity.sh [ozet_dosyasi] [log_dizini]
#
# Ortam değişkenleri (varsayılanlar yerel portable PG16 kurulumuna göre):
#   PGBIN   PostgreSQL 16 ikililerinin dizini  (pg_dump BURADAN PATH'e eklenir —
#           eklenmezse test_platform_backups_postgresql.py "pg_dump bulunamadı"
#           ile düşer)
#   PGHOST / PGPORT / PGUSER / PGPASSWORD / PGTESTDB
#   VENV_PY Testleri koşacak python
set -u

PGBIN="${PGBIN:-/c/pgdl/extracted/pgsql/bin}"
PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5433}"
PGUSER="${PGUSER:-yerelhesap}"
export PGPASSWORD="${PGPASSWORD:-yerelhesap}"
DB="${PGTESTDB:-yerelhesap_test}"

BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
VENV_PY="${VENV_PY:-$BACKEND/.venv/Scripts/python.exe}"
URL="postgresql+psycopg://${PGUSER}:${PGPASSWORD}@localhost:${PGPORT}/${DB}"

OUT="${1:-pg16_ozet.txt}"
LOGDIR="${2:-pg16_loglar}"

# pg_dump/pg_restore smoke'u için ikililer PATH'te olmalı.
export PATH="$PGBIN:$PATH"

mkdir -p "$LOGDIR"
cd "$BACKEND" || exit 1

psql_do() {
  "$PGBIN/psql.exe" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -tAc "$1" >/dev/null 2>&1
}

{
  echo "# Gerçek PostgreSQL 16 koşumu"
  echo "commit: $(git rev-parse --short HEAD)  ($(git log -1 --format=%s))"
  echo "baslangic: $(date '+%Y-%m-%d %H:%M:%S')"
  echo ""
  printf '%-58s %-42s %s\n' "DOSYA" "ENV" "SONUC"
} > "$OUT"

files=$(ls test_*_postgresql.py | sort)
total=$(echo "$files" | wc -l)
i=0
failed=0

for f in $files; do
  i=$((i + 1))
  # Her dosya kendi *_TEST_DATABASE_URL değişkenini okur; bulunamazsa DATABASE_URL.
  v=$(grep -oE "['\"][A-Z0-9_]*TEST_DATABASE_URL['\"]" "$f" | head -1 | tr -d "'\"")
  [ -z "$v" ] && v=DATABASE_URL

  psql_do "DROP DATABASE IF EXISTS ${DB} WITH (FORCE);"
  psql_do "CREATE DATABASE ${DB};"

  log="$LOGDIR/${f%.py}.log"
  env "${v}=$URL" DATABASE_URL="$URL" APP_TEST_DATABASE_URL="$URL" \
      PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
      "$VENV_PY" -m pytest -q "$f" > "$log" 2>&1
  rc=$?

  line=$(grep -E "passed|failed|error" "$log" | tail -1 | sed 's/[[:space:]]*$//')
  [ -z "$line" ] && line="(cikti yok, rc=$rc)"
  status="OK"
  if [ $rc -ne 0 ]; then status="BASARISIZ"; failed=$((failed + 1)); fi

  printf '%-58s %-42s %s | %s\n' "$f" "$v" "$status" "$line" >> "$OUT"
  echo "[$i/$total] $f -> $status | $line"
done

{
  echo ""
  echo "bitis: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "TOPLAM: $total dosya"
  echo "BASARISIZ: $failed"
} >> "$OUT"

echo "Ozet: $OUT"
exit $(( failed > 0 ))
