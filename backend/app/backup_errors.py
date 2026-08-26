class BackupError(RuntimeError):
    pass


class MaintenanceActiveError(BackupError):
    pass
