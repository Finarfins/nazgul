#!/usr/bin/env bash
#
# Yüklenmiş bir container imajının KİMLİĞİNİ ve OCI revision etiketini beklenen
# değerlere karşı ölçen KAPI. Eşitsizlikte İKİ DEĞERİ DE adlandırıp sıfırdan
# farklı çıkar.
#
# NEDEN AYRI BİR BETİK — bu, üçüncü turdur:
#   #6  kapıyı kurdu; sözleşme (K5) üç SABİT DİZGE arıyordu ve dört kaçış
#       yeşil ölçüldü (`|| true`, `exit 1` yerine `echo`, ikinci bir
#       `expected_image_id` ataması, `|| [ 1 = 1 ]`).
#   #7  K5'i akıllandırdı: karşılaştırmayı İZLEYEN satırda `exit 1`, tek atama,
#       beklentinin kaynağı. Dört kaçış daha bulundu ve ÜÇÜ DE bu kontrollerden
#       geçti (`image_id="$expected_image_id"`, `if false; then … fi`,
#       alt kabuk + dışarıda `|| true`, karşılaştırmadan önce `exit 0`).
#
# İki tur da metin üzerinden bir AKIŞ DENETİMİ özelliği kanıtlamaya çalıştı.
# Konum, başarısızlık semantiği için yeterli bir değişmez değildir. Bu yüzden
# mantık buraya taşındı: sözleşme artık bu betiği METİN olarak incelemek yerine
# ÇALIŞTIRIP kırmızı vermesini ŞART KOŞUYOR (bkz. `deploy-sozlesme-testi.sh`
# K8). CI adımı da bu betiği çağırmaktan BAŞKA bir şey yapmaz; böylece
# metinle çivilenmesi gereken yüzey bir bloktan TEK SATIRA iner.
#
# Girdi ortam değişkenleriyle alınır (CI çağrı yerinin tek satır kalması için):
#   IMAJ_REF                 — ölçülecek yerel imaj referansı
#   BEKLENEN_IMAJ_KIMLIGI    — beklenen `.Id`
#   BEKLENEN_OCI_REVIZYONU   — beklenen org.opencontainers.image.revision
#
# Üçü de ZORUNLUDUR. Boş bir beklenti kapıyı sessizce etkisizleştirirdi; bu
# yüzden eksik girdi YEŞİL değil KIRMIZIDIR (fail-closed).
set -euo pipefail

hata() { printf '::error::%s\n' "$1" >&2; exit 1; }

[ -n "${IMAJ_REF:-}" ]               || hata "IMAJ_REF bos; olculecek imaj bildirilmedi"
[ -n "${BEKLENEN_IMAJ_KIMLIGI:-}" ]  || hata "BEKLENEN_IMAJ_KIMLIGI bos; kimlik karsilastirilamaz"
[ -n "${BEKLENEN_OCI_REVIZYONU:-}" ] || hata "BEKLENEN_OCI_REVIZYONU bos; revision karsilastirilamaz"

imaj_kimligi="$(docker image inspect "$IMAJ_REF" --format '{{.Id}}')"
oci_revizyonu="$(docker image inspect "$IMAJ_REF" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"

[ -n "$imaj_kimligi" ] || hata "artifact imaj ID bos: '$IMAJ_REF'"

# KİMLİK KARŞILAŞTIRMASI. `docker save` -> `docker load` `.Id`'yi AYNEN korur
# (ölçüldü), bu yüzden eşitsizlik yüklenen artifact'in container'ın TEST ETTİĞİ
# imaj OLMADIĞI demektir. OCI revision etiketi bu boşluğu KAPATMAZ: etiket
# imajın İÇERİĞİNE bağlı değildir, başka bir imaj aynı etiketi taşıyabilir.
[ "$imaj_kimligi" = "$BEKLENEN_IMAJ_KIMLIGI" ] \
  || hata "artifact imaji container'in TEST ETTIGI imaj degil: yuklenen='$imaj_kimligi' beklenen='$BEKLENEN_IMAJ_KIMLIGI'"

[ "$oci_revizyonu" = "$BEKLENEN_OCI_REVIZYONU" ] \
  || hata "artifact OCI revision yanlis: okunan='$oci_revizyonu' beklenen='$BEKLENEN_OCI_REVIZYONU'"

printf 'artifact_image_ref=%s\nartifact_image_id=%s\nartifact_oci_revision=%s\n' \
  "$IMAJ_REF" "$imaj_kimligi" "$oci_revizyonu"
