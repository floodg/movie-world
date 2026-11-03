"""Errors that are shown to the phone as an actionable job error."""


class PlaybackError(Exception):
    def __init__(self, message, code="failed", retryable=False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
