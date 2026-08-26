#!/usr/bin/env python3
"""publish-image.needs kümesini workflow job kümesine karşı fail-closed ölçer."""

from __future__ import annotations

import sys
from pathlib import Path


# Yayın bariyerinden bilinçli olarak çıkarılan job kimlikleri burada, gerekçeli
# yorumuyla açıkça listelenmelidir. Şu anda istisna YOKTUR. Yeni bir job eklemek
# K1'i kırmızı yapar; bu listeye sessiz veya örtük kabul eklenemez.
PUBLISH_NEEDS_EXCEPTIONS: frozenset[str] = frozenset(
    {
        # Örnek biçim: "bilincli-job-istisnasi",  # Neden yayın bariyerinde değil?
    }
)

# Bu koşul status-check fonksiyonu içermez; GitHub Actions böylece needs
# bağımlılıkları için örtük success() kapısını uygular. always()/failure()/
# cancelled() veya success() ile kurulan OR ifadeleri yayın bariyerini
# gevşetebileceği için parse edilmiş değer bununla tam eşit olmalıdır.
PUBLISH_IF_CONDITION = "github.event_name == 'push' && github.ref == 'refs/heads/develop'"


def fail(message: str) -> int:
    print(f"K1 YAML yayın bağımlılığı ölçülemedi/ihlal edildi: {message}")
    return 1


def continue_on_error_violations(value: object, path: str) -> list[str]:
    """Gating job içindeki bütün non-false continue-on-error alanlarını bul."""
    violations: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "continue-on-error" and child is not False:
                violations.append(f"{child_path}={child!r}")
            violations.extend(continue_on_error_violations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(continue_on_error_violations(child, f"{path}[{index}]"))
    return violations


def main() -> int:
    if len(sys.argv) != 2:
        return fail("kullanım: ci-yayin-needs-kapisi.py <ci.yml>")

    try:
        import yaml
    except Exception as exc:  # Import hatasının her biçimi fail-closed olmalı.
        return fail(f"YAML parser kullanılamıyor: {exc}")

    workflow_path = Path(sys.argv[1])
    try:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"workflow YAML parse edilemedi: {exc}")

    if not isinstance(workflow, dict):
        return fail("workflow kökü mapping değil")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return fail("jobs mapping yok veya boş")
    if not all(isinstance(job_id, str) and job_id for job_id in jobs):
        return fail("job kimlikleri geçerli string değil")

    publish_job = jobs.get("publish-image")
    if not isinstance(publish_job, dict):
        return fail("publish-image job'ı yok veya mapping değil")

    needs = publish_job.get("needs")
    if isinstance(needs, str):
        actual_needs = {needs}
    elif isinstance(needs, list) and all(isinstance(item, str) and item for item in needs):
        actual_needs = set(needs)
    else:
        return fail("publish-image.needs string veya string listesi değil")

    job_ids = set(jobs)
    unknown_exceptions = PUBLISH_NEEDS_EXCEPTIONS - job_ids
    if unknown_exceptions:
        return fail(f"istisna listesinde workflow'da olmayan job var: {sorted(unknown_exceptions)}")

    expected_needs = job_ids - {"publish-image"} - PUBLISH_NEEDS_EXCEPTIONS
    if actual_needs != expected_needs:
        missing = sorted(expected_needs - actual_needs)
        unexpected = sorted(actual_needs - expected_needs)
        return fail(
            "publish-image.needs tam küme eşitliği bozuk; "
            f"eksik={missing}, fazladan={unexpected}, "
            f"beklenen={sorted(expected_needs)}, gerçek={sorted(actual_needs)}"
        )

    continue_on_error = []
    for job_id in sorted(expected_needs):
        continue_on_error.extend(
            continue_on_error_violations(jobs[job_id], f"jobs.{job_id}")
        )
    if continue_on_error:
        return fail(
            "yayın bariyerindeki job/step continue-on-error ile fail-open: "
            f"{continue_on_error}"
        )

    publish_if = publish_job.get("if")
    if publish_if != PUBLISH_IF_CONDITION:
        return fail(
            "publish-image.if needs başarı kapısını gevşetiyor veya beklenen "
            f"develop-push koşulu değil; beklenen={PUBLISH_IF_CONDITION!r}, "
            f"gerçek={publish_if!r}"
        )

    print(
        "K1 publish-image.needs workflow'daki bütün diğer job'lara tam eşit "
        f"({len(actual_needs)} job, istisna={sorted(PUBLISH_NEEDS_EXCEPTIONS)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
