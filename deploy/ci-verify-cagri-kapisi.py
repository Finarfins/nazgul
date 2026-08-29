#!/usr/bin/env python3
"""verify-image-artifact ÇAĞRI YERİNİ YAML olarak fail-closed ölçer (K5).

NEDEN METİN GREP DEĞİL PARSE
  K5 iki turdur `ci.yml` metninde dizge aradı. Ölçülen kaçışlar grep'in
  göremediği yerlerdi, çünkü grep bir AKIŞ DENETİMİ özelliği kanıtlamaz:

  * adım-seviyesi `if: false`  — job-seviyesi `^    if:` deseni 4 boşluk
    arıyordu; adım 8 boşlukla yazılır, iş yeşil, betik hiç koşmaz.
  * yinelenen `run:` anahtarı — dosyada `run: ./deploy/artifact-imaj-kimlik-kapisi.sh`
    satırı DURUR, YAML'de son anahtar kazanır ve koşan komut `"true"` olur.
  * fazladan env (`BASH_ENV`, `PATH`) — zorunlu üç env satırı yerinde durur;
    `BASH_ENV` betiği kimlik bakmadan `exit 0` yaptırır, sahte `PATH` `docker`'ı
    beklenen kimliği echo'layan bir ikiliyle değiştirir.

  Bu kapı workflow'u YAML olarak yükler (yinelenen anahtar KIRMIZI),
  `verify-image-artifact` işinin anahtar kümesini KAPALI tutar ve doğrulama
  adımının `run` / `env` / `if` / `shell` değerlerini parse edilmiş nesneden
  okur. Metin satırı yerinde dururken parse edilmiş değer başkaysa, kapı
  kırmızıdır.

OLÇER
  * iş anahtarları tam olarak {needs, runs-on, timeout-minutes, permissions, steps}
    — `if`, `env`, `defaults`, `continue-on-error`, `container`, `services` yok
  * needs == [container], permissions == {contents: read}
  * tam dört adım, her adımın anahtar kümesi kapalı
  * checkout `with:` / başka depo yok
  * artifact adı `tested-production-image-${{ github.sha }}` ve
    `if-no-files-found: error`
  * doğrulama adımı: `if` yok, `shell` yok, `continue-on-error` yok,
    `run` TAM OLARAK `./deploy/artifact-imaj-kimlik-kapisi.sh`,
    env anahtarları TAM OLARAK üçlü (fazladan BASH_ENV/PATH yok)
  * workflow kökünde `env:` / `defaults:` yok (iş-dışı BASH_ENV kaçışı)
  * çağrılan betik depoda var ve çalıştırılabilir

OLCMEZ
  * betiğin İÇİNDE kapı olduğu — bunu K9 ÇALIŞTIRARAK ölçer
  * sözleşme betiğinin KENDİSİNİN düzenlenmesi
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

EXPECTED_RUN = "./deploy/artifact-imaj-kimlik-kapisi.sh"
EXPECTED_SCRIPT = "deploy/artifact-imaj-kimlik-kapisi.sh"
EXPECTED_ENV = {
    "IMAJ_REF": "yerel-hesap-pro:${{ github.sha }}",
    "BEKLENEN_IMAJ_KIMLIGI": "${{ needs.container.outputs.tested_image_id }}",
    "BEKLENEN_OCI_REVIZYONU": "${{ github.sha }}",
}
JOB_KEYS = frozenset({"needs", "runs-on", "timeout-minutes", "permissions", "steps"})
VERIFY_STEP_KEYS = frozenset({"name", "env", "run"})
DOWNLOAD_WITH = {
    "name": "tested-production-image-${{ github.sha }}",
    "path": "${{ runner.temp }}/tested-production-image",
    "if-no-files-found": "error",
}


class DuplicateKeyError(ValueError):
    """YAML mapping içinde aynı anahtar iki kez."""


def fail(message: str) -> int:
    print(f"K5 verify-image-artifact cagri yeri KIRMIZI: {message}")
    return 1


def load_workflow(path: Path) -> Any:
    try:
        import yaml
    except Exception as exc:
        return fail(f"YAML parser kullanilamiyor: {exc}")

    class StrictLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):  # type: ignore[no-untyped-def]
        loader.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            value = loader.construct_object(value_node, deep=deep)
            if key in mapping:
                raise DuplicateKeyError(f"yinelenen YAML anahtari: {key!r}")
            mapping[key] = value
        return mapping

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )

    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except DuplicateKeyError as exc:
        return fail(str(exc))
    except Exception as exc:
        return fail(f"workflow YAML parse edilemedi: {exc}")


def require_mapping(value: Any, where: str) -> dict[str, Any] | int:
    if not isinstance(value, dict):
        return fail(f"{where} mapping degil")
    if not all(isinstance(k, str) and k for k in value):
        return fail(f"{where} anahtarlari gecerli string degil")
    return value


def extra_keys(actual: frozenset[str], allowed: frozenset[str], where: str) -> str | None:
    unexpected = sorted(actual - allowed)
    missing = sorted(allowed - actual)
    if unexpected or missing:
        return (
            f"{where} anahtar kumesi kapali degil; "
            f"eksik={missing}, fazladan={unexpected}, izinli={sorted(allowed)}"
        )
    return None


def main() -> int:
    if len(sys.argv) != 2:
        return fail("kullanim: ci-verify-cagri-kapisi.py <ci.yml>")

    workflow_path = Path(sys.argv[1]).resolve()
    loaded = load_workflow(workflow_path)
    if isinstance(loaded, int):
        return loaded
    if not isinstance(loaded, dict):
        return fail("workflow koku mapping degil")
    workflow = loaded

    # PyYAML 1.1 `on:` anahtarını bool True yapar; kökün bütün anahtarlarını
    # string sanmak bu yüzden yanlış kırmızı olur. `env`/`defaults` stringdir.
    if "env" in workflow:
        return fail("workflow kokunde env var; BASH_ENV/PATH kacisi is disindan acilabilir")
    if "defaults" in workflow:
        return fail("workflow kokunde defaults var; shell sarmalayicisi butun run adimlarini sarar")

    jobs = require_mapping(workflow.get("jobs"), "jobs")
    if isinstance(jobs, int):
        return jobs
    job = require_mapping(jobs.get("verify-image-artifact"), "jobs.verify-image-artifact")
    if isinstance(job, int):
        return job

    keys_err = extra_keys(frozenset(job), JOB_KEYS, "verify-image-artifact")
    if keys_err:
        return fail(keys_err)
    if job.get("needs") != ["container"]:
        return fail(f"needs [container] degil: {job.get('needs')!r}")
    if job.get("runs-on") != "ubuntu-latest":
        return fail(f"runs-on ubuntu-latest degil: {job.get('runs-on')!r}")
    if job.get("timeout-minutes") != 12:
        return fail(f"timeout-minutes 12 degil: {job.get('timeout-minutes')!r}")
    perms = require_mapping(job.get("permissions"), "permissions")
    if isinstance(perms, int):
        return perms
    if perms != {"contents": "read"}:
        return fail(f"permissions tam {{contents: read}} degil: {perms!r}")

    steps = job.get("steps")
    if not isinstance(steps, list) or len(steps) != 4:
        return fail(f"adim listesi tam dort degil: {type(steps).__name__} len={getattr(steps, '__len__', lambda: '?')()}")
    if not all(isinstance(s, dict) for s in steps):
        return fail("her adim mapping olmali")

    checkout, download, load, verify = steps

    chk_err = extra_keys(frozenset(checkout), frozenset({"uses"}), "checkout adimi")
    if chk_err:
        return fail(chk_err)
    if checkout.get("uses") != "actions/checkout@v4":
        return fail(f"checkout uses actions/checkout@v4 degil: {checkout.get('uses')!r}")

    dl_err = extra_keys(frozenset(download), frozenset({"name", "uses", "with"}), "download adimi")
    if dl_err:
        return fail(dl_err)
    if download.get("name") != "Download tested production image":
        return fail(f"download adi beklenmiyor: {download.get('name')!r}")
    if download.get("uses") != "actions/download-artifact@v4":
        return fail(f"download uses actions/download-artifact@v4 degil: {download.get('uses')!r}")
    with_block = require_mapping(download.get("with"), "download.with")
    if isinstance(with_block, int):
        return with_block
    if with_block != DOWNLOAD_WITH:
        return fail(f"download.with tam esit degil: {with_block!r}")

    load_err = extra_keys(frozenset(load), frozenset({"name", "run"}), "load adimi")
    if load_err:
        return fail(load_err)
    if load.get("name") != "Load tested production image":
        return fail(f"load adi beklenmiyor: {load.get('name')!r}")
    if load.get("run") != (
        'gzip -dc "$RUNNER_TEMP/tested-production-image/tested-production-image.tar.gz" | docker load'
    ):
        return fail(f"load run beklenen gzip|docker load degil: {load.get('run')!r}")

    ver_err = extra_keys(frozenset(verify), VERIFY_STEP_KEYS, "verify adimi")
    if ver_err:
        return fail(ver_err)
    if verify.get("name") != "Verify loaded image identity and OCI revision":
        return fail(f"verify adi beklenmiyor: {verify.get('name')!r}")
    if verify.get("run") != EXPECTED_RUN:
        return fail(f"verify run TAM betik cagrisi degil: {verify.get('run')!r}")
    env = require_mapping(verify.get("env"), "verify.env")
    if isinstance(env, int):
        return env
    if env != EXPECTED_ENV:
        return fail(
            "verify.env tam uc anahtar ve beklenen degerler degil "
            f"(fazladan BASH_ENV/PATH kimlik kacisidir): {env!r}"
        )

    script = Path(__file__).resolve().parent / Path(EXPECTED_SCRIPT).name
    if not script.is_file() or not os_access_executable(script):
        return fail(f"kapi betigi yok veya calistirilabilir degil: {EXPECTED_SCRIPT}")

    print(
        "K5 verify-image-artifact cagri yeri YESIL: YAML parse, kapali anahtar "
        "kumesi, sarmalanmamis tek run, tam uc env, artifact adi ve "
        "if-no-files-found:error civili"
    )
    return 0


def os_access_executable(path: Path) -> bool:
    import os

    return os.access(path, os.X_OK)


if __name__ == "__main__":
    raise SystemExit(main())
