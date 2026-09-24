class PermanentError(Exception):
    """Do not retry: the job can never succeed (e.g. the PR was deleted)."""


class SkipJob(Exception):
    """Nothing to do (PR closed, superseded, already reviewed). The job is marked done."""
