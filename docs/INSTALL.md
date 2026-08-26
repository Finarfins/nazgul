# Kurulum Rehberi

## Gereksinimler

- Windows 10/11 veya güncel Linux
- Python 3.12
- Node.js 20+
- npm
- PostgreSQL 16 (production için)
- Git

## Depoyu indirme

```bash
git clone https://github.com/Finarfins/nazgul.git
cd nazgul
```

## Backend kurulumu

```bash
cd backend
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Migration:

```bash
alembic upgrade head
```

Backend başlatma:

```bash
uvicorn app.main:app --reload
```

## Frontend kurulumu

Yeni terminal açın:

```bash
cd frontend
npm install
npm run dev
```

## Testler

Backend:

```bash
cd backend
pytest
```

Frontend:

```bash
cd frontend
npm test
npm run build
```

## Güvenlik notları

- `.env` dosyasını commit etmeyin.
- Gerçek `*.db`, PostgreSQL dump ve yedek dosyalarını GitHub'a yüklemeyin.
- Production ortamında varsayılan admin parolasını kullanmayın.
- HTTPS ve Secure cookie ayarlarını etkinleştirin.

## Demo

Demo kayıtları seed betiğiyle oluşturulmalıdır. Ayrıntılar için `demo/README.md` dosyasına bakın.
