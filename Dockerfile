FROM node:22-alpine AS web-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Cloudflare Turnstile public site key. Vite inlines VITE_* at build time, so
# this MUST be present as an env var BEFORE `npm run build`; passing it only at
# runtime leaves import.meta.env.VITE_TURNSTILE_SITE_KEY undefined and the
# widget never renders. Same value as the backend's TURNSTILE_SITE_KEY (a public
# key, safe to embed); compose maps it in via build.args.
ARG VITE_TURNSTILE_SITE_KEY=
ENV VITE_TURNSTILE_SITE_KEY=$VITE_TURNSTILE_SITE_KEY
RUN NODE_OPTIONS="--max-old-space-size=2048" npm run build

FROM python:3.12-slim AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Full PostgreSQL logical backups/restores run inside the application container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# UID/GID SÖZLEŞMESİ — SABİT, DEĞİŞTİRMEYİN.
# 10001:10001, kalıcı sungur_data volume'undaki dosyaların sahibidir ve volume
# imaj yeniden derlendiğinde de yaşamaya devam eder. `adduser --system` UID'yi
# 100-999 havuzundan DİNAMİK seçer; taban imaj (python:3.12-slim) bir gün yeni
# bir sistem kullanıcısı eklerse UID kayar ve volume'daki tüm attachment/yedek
# dosyaları uygulamaya okunamaz hâle gelir. Volume kalıcı olduğu için UID artık
# bir veri sözleşmesidir, derleme detayı değil.
# Not: useradd, 10001 > SYS_UID_MAX (999) olduğu için derlemede bir UYARI basar.
# Kozmetiktir — komut 0 ile çıkar, kullanıcı yine /usr/sbin/nologin kabuklu
# sistem kullanıcısı olur. Uyarıdan kurtulmak için UID'yi 999 altına çekmeyin:
# o aralık taban imajın kendi sistem kullanıcılarıyla çakışmaya açıktır.
RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /app app

# Kalıcı veri kökü: attachment'lar (SUNGUR_DATA_DIR/attachments) ve platform
# yedekleri (SUNGUR_DATA_DIR/backups). Dizinin imajda ve `app` sahipliğinde
# VAR OLMASI şarttır: Docker boş bir named volume'u bağlarken imajdaki dizinin
# sahipliğini ve iznini volume'a kopyalar (copy-up). Dizin imajda yoksa volume
# root:root doğar, non-root uygulama içine yazamaz ve tek çare ya root'a düşmek
# ya da her açılışta chown etmek olurdu — ikisi de istenmiyor.
# Bağlama noktası docker-compose.prod.yml'deki sungur_data volume'udur.
RUN install -d -o app -g app -m 750 /opt/sungur-data

WORKDIR /app

# Install the fully pinned, hashed lock for reproducible production builds.
# requirements.lock is compiled from requirements.txt inside this same
# python:3.12-slim base (see backend/requirements.lock header). requirements.txt
# is copied too because the test stage's requirements-dev.txt references it.
COPY backend/requirements.txt backend/requirements.lock ./backend/
RUN pip install --no-cache-dir --require-hashes -r backend/requirements.lock

COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app --from=web-build /app/frontend/dist ./frontend/dist

FROM runtime-base AS test
USER root
COPY backend/requirements-dev.txt ./backend/requirements-dev.txt
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
USER app

FROM runtime-base AS production
USER root
# Üretim imajı test varlıklarını taşımamalı; `test` stage ise bunları korumak
# için runtime-base'ten türemeye devam eder. Kök olarak sileriz, sonra
# uygulamayı yeniden non-root'a alırız. İçe aktarım denemesi f623190'da
# temizlenmiş ağaçla `app.main` import'unu doğruladı.
# `backend/sandbox/izibiz_smoke.py` GERCEK SOAP cagrisi yapar; uretim imajinda
# bulunmasi, kabuga erisen birinin canli e-fatura ucuna istek atabilmesi
# demektir. app/einvoice/*.py icindeki `backend/sandbox/izibiz_smoke.py`
# gecisleri YALNIZCA docstring KANIT ATIFIDIR, import veya dosya okuma degil
# (olcum: ayni yollarda `import|open(|Path(|read_text|load` ile birlikte
# arandiginda 0 satir). requirements-dev.txt'in silinmesi `test` stage'i
# ETKILEMEZ: orasi dosyayi 62. satirda AYRICA kopyalar. tools/ dizini degil,
# yalniz capture_frontend_fixtures.py silinir; report_receivable_*.py KALIR.
RUN rm -rf /app/backend/tests /app/backend/test_*.py /app/backend/donmus_saat.py /app/backend/conftest.py /app/backend/pytest.ini /app/backend/run_isolated_tests.py /app/backend/isolated_test_reporter.py /app/backend/aggregate_isolated_test_reports.py /app/backend/merge_postgresql_test_reports.py /app/backend/non_twin_skip_exceptions.json /app/backend/sandbox /app/backend/requirements-dev.txt /app/backend/LEGACY_TEST_MIGRATION_PLAN.md /app/backend/tools/capture_frontend_fixtures.py
USER app
EXPOSE 5050
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/api/live',timeout=3)"
CMD ["python", "backend/run.py"]
