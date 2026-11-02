class FeatureError(Exception):
    """Raised when a feature run fails but has partial record data to persist in job_runs."""

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message)
        self.data = data or {}
